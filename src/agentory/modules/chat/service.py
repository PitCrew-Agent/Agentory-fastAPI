"""채팅 서비스: 세션 관리 + Agent 오케스트레이터 연동 (BE_CHAT01_QUERY01)

Agent 그래프 스트리밍을 SSE 이벤트로 흘리며, 질의·응답과 추론 기록을 DB에 저장
비스트리밍 응답은 동일 스트림을 소비해 구성 (단일 실행 경로)
"""

import re
import uuid
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentory.common.events import SSEEvent
from agentory.common.exceptions import ExternalServiceError
from agentory.core.config import get_settings
from agentory.modules.agent.runner import RECURSION_LIMIT, get_graph, initial_state
from agentory.modules.agent.streaming import stream_agent_events
from agentory.modules.chat.models import ChatMessage, ChatSession
from agentory.modules.chat.schemas import (
    ChatMessageItem,
    ChatResponse,
    ChatSessionDetail,
    ChatSessionSummary,
    ReasoningStep,
)

DEFAULT_USER = "anonymous"  # 라우터가 JWT sub를 넘기지 못한 경우의 폴백값
TITLE_MAX_LEN = 30  # 히스토리 제목(첫 질문 요약) 절삭 길이
DEFAULT_TITLE = "새 대화"  # 첫 사용자 질의가 없는 세션의 폴백 제목
HISTORY_LIST_LIMIT = 100  # 히스토리 목록 최대 세션 수


async def _ensure_session(
    db, session_id: uuid.UUID, user_sub: str, equipment_id: str | None = None
) -> None:
    # 세션이 없으면 생성 (클라이언트 지정 session_id 사용), 첫 질의의 선택 설비를 세션에 고정
    exists = await db.scalar(select(ChatSession).where(ChatSession.session_id == session_id))
    if exists is None:
        db.add(ChatSession(session_id=session_id, user_sub=user_sub, equipment_id=equipment_id))
        await db.commit()


async def _load_history(db, session_id: uuid.UUID) -> list:
    # 이전 user/assistant 발화를 LangChain 메시지로 로드 (멀티턴 컨텍스트)
    # 최근 N개만 로드해 대화가 길어질수록 프롬프트가 커지는 누적 지연 방지
    limit = get_settings().agent_history_max_messages
    rows = await db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    history = []
    for row in reversed(list(rows)):  # 최신 N개를 다시 시간순으로 복원
        if row.role == "user":
            history.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            history.append(AIMessage(content=row.content))
    return history


async def stream_chat(
    session_factory: async_sessionmaker,
    session_id: str,
    message: str,
    user_sub: str = DEFAULT_USER,
    equipment_id: str | None = None,
) -> AsyncIterator[SSEEvent]:
    # 질의를 그래프에 흘려 SSE 이벤트를 방출하고, 완료 후 응답·추론 기록 저장
    sid = uuid.UUID(session_id)
    async with session_factory() as db:
        await _ensure_session(db, sid, user_sub, equipment_id)
        history = await _load_history(db, sid)
        db.add(ChatMessage(session_id=sid, role="user", content=message))
        await db.commit()

    graph = await get_graph()
    # 대시보드에서 선택된 설비를 컨텍스트로 시드 (NEW_TWIN01_CHATCTX01)
    state = initial_state(message, history, equipment_id=equipment_id)

    answer_parts: list[str] = []
    trace_steps: list[dict] = []
    citations: list[dict] = []
    grounded = None
    suggested: list[str] = []
    errored = False

    async for event in stream_agent_events(
        graph, state, config={"recursion_limit": RECURSION_LIMIT}
    ):
        if event.type == "answer":
            answer_parts.append(event.delta)
        elif event.type in ("thought", "action", "observation"):
            trace_steps.append(event.model_dump())
        elif event.type == "error":
            errored = True
        elif event.type == "done":
            citations = [c.model_dump() for c in event.citations]
            grounded = event.grounded
            suggested = event.suggested_questions
        yield event

    # 오류로 끝난 경우 답변이 없으므로 assistant 메시지 미저장
    if errored:
        return

    # 최종 답변·추론 기록 저장 (trace jsonb)
    async with session_factory() as db:
        db.add(
            ChatMessage(
                session_id=sid,
                role="assistant",
                content="".join(answer_parts),
                trace={
                    "steps": trace_steps,
                    "citations": citations,
                    "grounded": grounded,
                    "suggested_questions": suggested,
                },
            )
        )
        await db.commit()


async def run_query(
    session_factory: async_sessionmaker,
    session_id: str,
    message: str,
    user_sub: str = DEFAULT_USER,
    equipment_id: str | None = None,
) -> ChatResponse:
    # 스트림을 내부 소비해 비스트리밍 응답 구성
    answer_parts: list[str] = []
    steps: list[ReasoningStep] = []
    citations = []
    suggested: list[str] = []
    async for event in stream_chat(
        session_factory, session_id, message, user_sub, equipment_id=equipment_id
    ):
        if event.type == "answer":
            answer_parts.append(event.delta)
        elif event.type == "thought":
            steps.append(ReasoningStep(step=event.step, thought=event.content))
        elif event.type == "action":
            steps.append(
                ReasoningStep(step=event.step, tool=event.tool, tool_input=event.tool_input)
            )
        elif event.type == "observation":
            steps.append(ReasoningStep(step=event.step, tool=event.tool, observation=event.content))
        elif event.type == "error":
            # 비스트리밍은 빈 응답 대신 예외로 표면화
            raise ExternalServiceError(event.message, code=event.code)
        elif event.type == "done":
            citations = event.citations
            suggested = event.suggested_questions
    return ChatResponse(
        answer="".join(answer_parts),
        reasoning_steps=steps,
        citations=citations,
        suggested_questions=suggested,
    )


def _derive_title(content: str | None, equipment_id: str | None = None) -> str:
    # 첫 사용자 질문을 제목으로 절삭, 없으면 기본 제목 (BE_CHAT03_HISTORY01)
    text = (content or "").strip()
    if text and equipment_id:
        # 배지로 이미 표시되는 앞쪽 장비id는 제목에서 제거 (중복 방지)
        # 장비id 뒤가 구분자·공백·문자열 끝일 때만 제거해 유사 id 오탐 방지 (예: A01 vs A011)
        text = re.sub(
            rf"^{re.escape(equipment_id)}(\s*[-–:·,]\s*|\s+|$)",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
    if not text:
        return DEFAULT_TITLE
    return text if len(text) <= TITLE_MAX_LEN else text[:TITLE_MAX_LEN] + "…"


async def list_sessions(db: AsyncSession, user_sub: str) -> list[ChatSessionSummary]:
    # 본인 대화 세션을 최신순으로, 장비·제목·개수·마지막 시각과 함께 반환 (삭제 세션 제외)
    sessions = list(
        await db.scalars(
            select(ChatSession)
            .where(ChatSession.user_sub == user_sub, ChatSession.deleted_at.is_(None))
            .order_by(ChatSession.created_at.desc())
            .limit(HISTORY_LIST_LIMIT)
        )
    )
    if not sessions:
        return []

    sids = [s.session_id for s in sessions]
    # 세션별 메시지 개수·마지막 시각 집계 (N+1 방지)
    agg_rows = await db.execute(
        select(
            ChatMessage.session_id,
            func.count(ChatMessage.message_id),
            func.max(ChatMessage.created_at),
        )
        .where(ChatMessage.session_id.in_(sids))
        .group_by(ChatMessage.session_id)
    )
    agg = {sid: (cnt, last) for sid, cnt, last in agg_rows}

    # 세션별 첫 사용자 질문(제목 원천), DISTINCT ON으로 세션당 최초 1건
    title_rows = await db.execute(
        select(ChatMessage.session_id, ChatMessage.content)
        .where(ChatMessage.session_id.in_(sids), ChatMessage.role == "user")
        .distinct(ChatMessage.session_id)
        .order_by(ChatMessage.session_id, ChatMessage.created_at.asc())
    )
    first_user = {sid: content for sid, content in title_rows}

    summaries = []
    for s in sessions:
        count, last = agg.get(s.session_id, (0, None))
        summaries.append(
            ChatSessionSummary(
                session_id=str(s.session_id),
                equipment_id=s.equipment_id,
                title=_derive_title(first_user.get(s.session_id), s.equipment_id),
                created_at=s.created_at,
                last_message_at=last,
                message_count=count,
            )
        )
    return summaries


async def get_session_detail(
    db: AsyncSession, session_id: str, user_sub: str
) -> ChatSessionDetail | None:
    # 본인 세션만 상세 조회, 미존재·삭제·타인 소유·잘못된 ID는 None (라우터 404)
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.session_id == sid,
            ChatSession.user_sub == user_sub,
            ChatSession.deleted_at.is_(None),
        )
    )
    if session is None:
        return None

    rows = list(
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == sid)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.message_id.asc())
        )
    )
    first_user = next((r.content for r in rows if r.role == "user"), None)
    return ChatSessionDetail(
        session_id=str(sid),
        equipment_id=session.equipment_id,
        title=_derive_title(first_user, session.equipment_id),
        created_at=session.created_at,
        messages=[
            ChatMessageItem(
                message_id=r.message_id,
                role=r.role,
                content=r.content,
                trace=r.trace,
                created_at=r.created_at,
            )
            for r in rows
        ],
    )


async def delete_session(db: AsyncSession, session_id: str, user_sub: str) -> bool:
    # 본인 세션 soft delete, 미존재·삭제됨·타인 소유·잘못된 ID는 False (라우터 404)
    # 메시지·trace는 평가 재사용 위해 보존, 삭제 성공(1건)에만 커밋
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return False
    result = await db.execute(
        update(ChatSession)
        .where(
            ChatSession.session_id == sid,
            ChatSession.user_sub == user_sub,
            ChatSession.deleted_at.is_(None),
        )
        .values(deleted_at=func.now())
    )
    if result.rowcount == 0:
        return False
    await db.commit()
    return True

"""채팅 서비스: 세션 관리 + Agent 오케스트레이터 연동 (BE_CHAT01_QUERY01)

Agent 그래프 스트리밍을 SSE 이벤트로 흘리며, 질의·응답과 추론 기록을 DB에 저장
비스트리밍 응답은 동일 스트림을 소비해 구성 (단일 실행 경로)
"""

import uuid
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentory.common.events import SSEEvent
from agentory.common.exceptions import ExternalServiceError
from agentory.core.config import get_settings
from agentory.modules.agent.runner import RECURSION_LIMIT, get_graph, initial_state
from agentory.modules.agent.streaming import stream_agent_events
from agentory.modules.chat.models import ChatMessage, ChatSession
from agentory.modules.chat.schemas import ChatResponse, ReasoningStep

DEFAULT_USER = "anonymous"  # 라우터가 JWT sub를 넘기지 못한 경우의 폴백값


async def _ensure_session(db, session_id: uuid.UUID, user_sub: str) -> None:
    # 세션이 없으면 생성 (클라이언트 지정 session_id 사용)
    exists = await db.scalar(select(ChatSession).where(ChatSession.session_id == session_id))
    if exists is None:
        db.add(ChatSession(session_id=session_id, user_sub=user_sub))
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
        await _ensure_session(db, sid, user_sub)
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

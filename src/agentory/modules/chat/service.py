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
from agentory.modules.agent.runner import RECURSION_LIMIT, get_graph, initial_state
from agentory.modules.agent.streaming import stream_agent_events
from agentory.modules.chat.models import ChatMessage, ChatSession
from agentory.modules.chat.schemas import ChatResponse, ReasoningStep

DEFAULT_USER = "anonymous"  # TODO(안민호): 인증 연동 후 JWT sub로 대체


async def _ensure_session(db, session_id: uuid.UUID, user_sub: str) -> None:
    # 세션이 없으면 생성 (클라이언트 지정 session_id 사용)
    exists = await db.scalar(select(ChatSession).where(ChatSession.session_id == session_id))
    if exists is None:
        db.add(ChatSession(session_id=session_id, user_sub=user_sub))
        await db.commit()


async def _load_history(db, session_id: uuid.UUID) -> list:
    # 이전 user/assistant 발화를 LangChain 메시지로 로드 (멀티턴 컨텍스트)
    rows = await db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    history = []
    for row in rows:
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
) -> AsyncIterator[SSEEvent]:
    # 질의를 그래프에 흘려 SSE 이벤트를 방출하고, 완료 후 응답·추론 기록 저장
    sid = uuid.UUID(session_id)
    async with session_factory() as db:
        await _ensure_session(db, sid, user_sub)
        history = await _load_history(db, sid)
        db.add(ChatMessage(session_id=sid, role="user", content=message))
        await db.commit()

    graph = await get_graph()
    state = initial_state(message, history)

    answer_parts: list[str] = []
    trace_steps: list[dict] = []
    citations: list[dict] = []
    grounded = None
    suggested: list[str] = []

    async for event in stream_agent_events(
        graph, state, config={"recursion_limit": RECURSION_LIMIT}
    ):
        if event.type == "answer":
            answer_parts.append(event.delta)
        elif event.type in ("thought", "action", "observation"):
            trace_steps.append(event.model_dump())
        elif event.type == "done":
            citations = [c.model_dump() for c in event.citations]
            grounded = event.grounded
            suggested = event.suggested_questions
        yield event

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
) -> ChatResponse:
    # 스트림을 내부 소비해 비스트리밍 응답 구성
    answer_parts: list[str] = []
    steps: list[ReasoningStep] = []
    citations = []
    suggested: list[str] = []
    async for event in stream_chat(session_factory, session_id, message, user_sub):
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
        elif event.type == "done":
            citations = event.citations
            suggested = event.suggested_questions
    return ChatResponse(
        answer="".join(answer_parts),
        reasoning_steps=steps,
        citations=citations,
        suggested_questions=suggested,
    )

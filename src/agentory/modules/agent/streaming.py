"""그래프 스트리밍 → SSE 이벤트 변환 (BE_CHAT01_STREAM01, 설계 문서 §4.6)

graph.astream(updates·messages)를 agentory.common.events 계약으로 변환
매핑 로직은 순수 함수로 분리해 단위 테스트가 그래프 없이 검증 가능
"""

import itertools
from collections.abc import AsyncIterator
from typing import Any

from agentory.common.events import (
    ActionEvent,
    AgentName,
    AnswerEvent,
    Citation,
    DoneEvent,
    ObservationEvent,
    SSEEvent,
    ThoughtEvent,
)
from agentory.modules.agent.supervisor.finalizer import make_finalizer_node  # noqa: F401
from agentory.modules.agent.supervisor.graph import FINALIZER, GROUNDING

_WORKER_AGENTS = {a.value for a in AgentName}


def _agent(name: str) -> AgentName | None:
    # 노드명을 SSE agent 값으로 변환, 미매핑 노드는 None
    return AgentName(name) if name in _WORKER_AGENTS else None


def map_updates_chunk(chunk: dict[str, Any], step: int) -> list[SSEEvent]:
    # updates 모드 청크(노드별 상태 델타)를 thought/action/observation 이벤트로 변환
    events: list[SSEEvent] = []
    for node, update in chunk.items():
        if node == "supervisor":
            reason = update.get("route_reason")
            if reason:
                events.append(ThoughtEvent(step=step, agent=AgentName.SUPERVISOR, content=reason))
        elif node.endswith("_tools"):
            agent = _agent(node[: -len("_tools")])
            for msg in update.get("messages", []):
                events.append(
                    ObservationEvent(
                        step=step,
                        agent=agent or AgentName.SUPERVISOR,
                        tool=getattr(msg, "name", "") or "",
                        content=msg.content,
                    )
                )
        elif (agent := _agent(node)) is not None:
            for msg in update.get("messages", []):
                for call in getattr(msg, "tool_calls", None) or []:
                    events.append(
                        ActionEvent(
                            step=step,
                            agent=agent,
                            tool=call["name"],
                            tool_input=call["args"],
                        )
                    )
    return events


def map_messages_chunk(chunk: tuple) -> list[SSEEvent]:
    # messages 모드 청크(LLM 토큰)를 answer 이벤트로 변환, Finalizer 노드만 대상
    msg_chunk, meta = chunk
    if meta.get("langgraph_node") != FINALIZER:
        return []
    content = getattr(msg_chunk, "content", "")
    return [AnswerEvent(delta=content)] if content else []


async def stream_agent_events(
    graph, state: dict, config: dict | None = None
) -> AsyncIterator[SSEEvent]:
    # 그래프 실행 이벤트를 SSE 이벤트 순서대로 방출, 종료 시 done(인용·grounded) 부착
    counter = itertools.count(1)
    citations: list[dict] = []
    grounded: bool | None = None

    async for mode, chunk in graph.astream(
        state, config=config, stream_mode=["updates", "messages"]
    ):
        if mode == "messages":
            for event in map_messages_chunk(chunk):
                yield event
            continue
        # updates 모드: 종료 노드 산출물 수집 + 이벤트 방출
        for node, update in chunk.items():
            if node == FINALIZER:
                citations = update.get("citations", citations)
            elif node == GROUNDING:
                grounded = update.get("grounded", grounded)
        for event in map_updates_chunk(chunk, next(counter)):
            yield event

    yield DoneEvent(citations=[Citation(**c) for c in citations], grounded=grounded)

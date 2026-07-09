"""그래프 스트리밍 → SSE 이벤트 변환 (BE_CHAT01_STREAM01, 설계 문서 §4.6)

graph.astream(updates·messages)를 agentory.common.events 계약으로 변환
매핑 로직은 순수 함수로 분리해 단위 테스트가 그래프 없이 검증 가능
"""

import itertools
import logging
from collections.abc import AsyncIterator
from typing import Any

from langgraph.errors import GraphRecursionError

from agentory.common.events import (
    ActionEvent,
    AgentName,
    AnswerEvent,
    Citation,
    DoneEvent,
    ErrorEvent,
    ObservationEvent,
    SSEEvent,
    ThoughtEvent,
)
from agentory.modules.agent.supervisor.finalizer import make_finalizer_node  # noqa: F401
from agentory.modules.agent.supervisor.graph import FINALIZER, GROUNDING, SUGGEST

log = logging.getLogger(__name__)

_WORKER_AGENTS = {a.value for a in AgentName}

# 재귀 상한 초과 시 사용자 안내 (AI_AGENT03_FALLBACK01)
_RECURSION_MESSAGE = "분석 단계가 한도를 초과했습니다 질문을 좁혀 다시 시도해 주세요"
# 그 외 그래프 실행 실패 시 사용자 안내
_AGENT_ERROR_MESSAGE = "일시적인 오류로 답변을 완료하지 못했습니다 잠시 후 다시 시도해 주세요"


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
    suggested: list[str] = []

    # 그래프 실행 중 예외는 error 이벤트로 전달 후 done으로 마감 (계약: error → done)
    try:
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
                elif node == SUGGEST:
                    suggested = update.get("suggested_questions", suggested)
            for event in map_updates_chunk(chunk, next(counter)):
                yield event
    except GraphRecursionError:
        log.warning("[stream] 재귀 상한(%s) 초과", config)
        yield ErrorEvent(code="RECURSION_LIMIT", message=_RECURSION_MESSAGE)
    except Exception:
        log.exception("[stream] 그래프 실행 실패")
        yield ErrorEvent(code="AGENT_ERROR", message=_AGENT_ERROR_MESSAGE)

    yield DoneEvent(
        citations=[Citation(**c) for c in citations],
        grounded=grounded,
        suggested_questions=suggested,
    )

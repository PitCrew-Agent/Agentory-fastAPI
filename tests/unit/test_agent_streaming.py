"""SSE 스트리밍 변환·Finalizer 단위 테스트 (BE_CHAT01_STREAM01 / NEW_TRUST01)

그래프 없이 매핑 순수 함수와 인용 수집 로직을 검증
"""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agentory.modules.agent.streaming import (
    map_messages_chunk,
    map_updates_chunk,
    stream_agent_events,
)
from agentory.modules.agent.supervisor.finalizer import _collect_citations


def test_supervisor_update_maps_to_thought():
    chunk = {"supervisor": {"route_reason": "센서 로그 필요", "next": "data_analysis"}}
    events = map_updates_chunk(chunk, step=1)
    assert len(events) == 1
    assert events[0].type == "thought"
    assert events[0].agent == "supervisor"
    assert events[0].content == "센서 로그 필요"


def test_worker_tool_calls_map_to_action():
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "get_sensor_logs", "args": {"line_name": "B라인"}, "id": "c1"}],
    )
    events = map_updates_chunk({"data_analysis": {"messages": [msg]}}, step=2)
    assert len(events) == 1
    assert events[0].type == "action"
    assert events[0].tool == "get_sensor_logs"
    assert events[0].tool_input == {"line_name": "B라인"}


def test_tool_node_maps_to_observation():
    obs = ToolMessage(content="EQP-003 65도", tool_call_id="c1", name="get_sensor_logs")
    events = map_updates_chunk({"data_analysis_tools": {"messages": [obs]}}, step=3)
    assert len(events) == 1
    assert events[0].type == "observation"
    assert events[0].tool == "get_sensor_logs"


def test_planner_maps_to_thought_and_action():
    # 오케스트레이터 Planner: 판단 문장은 thought, 도구 호출은 도구별 agent action (#139)
    from agentory.common.events import AgentName

    msg = AIMessage(
        content="B라인 센서부터 확인",
        tool_calls=[{"name": "get_sensor_logs", "args": {"line_name": "B라인"}, "id": "c1"}],
    )
    tmap = {"get_sensor_logs": AgentName.DATA_ANALYSIS}
    events = map_updates_chunk({"planner": {"messages": [msg]}}, step=1, tool_agent_map=tmap)
    assert [e.type for e in events] == ["thought", "action"]
    assert events[0].content == "B라인 센서부터 확인"
    assert events[1].agent == "data_analysis"
    assert events[1].tool == "get_sensor_logs"


def test_fetch_maps_to_observation_with_agent():
    # 병렬 Fetch 결과가 도구별 agent로 observation 변환 (#139)
    from agentory.common.events import AgentName

    obs = ToolMessage(content="EQP-003 65도", tool_call_id="c1", name="search_manuals")
    tmap = {"search_manuals": AgentName.KNOWLEDGE}
    events = map_updates_chunk({"fetch": {"messages": [obs]}}, step=2, tool_agent_map=tmap)
    assert len(events) == 1
    assert events[0].type == "observation"
    assert events[0].agent == "knowledge"
    assert events[0].tool == "search_manuals"


def test_unmapped_tool_falls_back_to_supervisor_agent():
    # 매핑에 없는 도구는 supervisor agent로 폴백해 이벤트 유실 방지 (#139)
    obs = ToolMessage(content="결과", tool_call_id="c1", name="unknown_tool")
    events = map_updates_chunk({"fetch": {"messages": [obs]}}, step=1)
    assert events[0].agent == "supervisor"


def test_finalizer_tokens_map_to_answer():
    chunk = (AIMessage(content="EQP-003에서 "), {"langgraph_node": "finalizer"})
    events = map_messages_chunk(chunk)
    assert events[0].type == "answer"
    assert events[0].delta == "EQP-003에서 "


def test_non_finalizer_tokens_ignored():
    chunk = (AIMessage(content="라우팅 중"), {"langgraph_node": "supervisor"})
    assert map_messages_chunk(chunk) == []


class _RaisingGraph:
    # astream 첫 반복에서 지정 예외를 던지는 가짜 그래프
    def __init__(self, exc):
        self._exc = exc

    def astream(self, state, config=None, stream_mode=None):
        exc = self._exc

        async def gen():
            raise exc
            yield  # 제너레이터 성립용, 도달 안함

        return gen()


async def _collect(graph):
    return [event async for event in stream_agent_events(graph, {}, config={})]


@pytest.mark.asyncio
async def test_graph_exception_emits_error_then_done():
    # 그래프 실행 실패 시 계약대로 error → done 순서로 방출
    events = await _collect(_RaisingGraph(RuntimeError("boom")))
    assert [e.type for e in events] == ["error", "done"]
    assert events[0].code == "AGENT_ERROR"
    assert events[0].message


@pytest.mark.asyncio
async def test_recursion_limit_emits_dedicated_code():
    # 재귀 상한 초과는 전용 코드로 구분
    events = await _collect(_RaisingGraph(GraphRecursionError("limit")))
    assert events[0].type == "error"
    assert events[0].code == "RECURSION_LIMIT"
    assert events[-1].type == "done"


def test_collect_citations_from_observations():
    messages = [
        ToolMessage(
            content='{"timestamp": "2026-07-03T06:20:00+00:00", "temperature": 65}',
            tool_call_id="c1",
            name="get_sensor_logs",
        ),
        ToolMessage(
            content="MAN-ETC-042 냉각수 밸브 압력 저하 시 ERR-402 발생",
            tool_call_id="c2",
            name="search_manuals",
        ),
    ]
    citations = _collect_citations(messages)
    doc_ids = {c["doc_id"] for c in citations}
    assert "MAN-ETC-042" in doc_ids
    assert "telemetry" in doc_ids

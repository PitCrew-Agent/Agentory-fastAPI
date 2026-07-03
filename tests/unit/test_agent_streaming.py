"""SSE 스트리밍 변환·Finalizer 단위 테스트 (BE_CHAT01_STREAM01 / NEW_TRUST01)

그래프 없이 매핑 순수 함수와 인용 수집 로직을 검증
"""

from langchain_core.messages import AIMessage, ToolMessage

from agentory.modules.agent.streaming import map_messages_chunk, map_updates_chunk
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
        tool_calls=[{"name": "get_sensor_logs", "args": {"line_name": "B-Line"}, "id": "c1"}],
    )
    events = map_updates_chunk({"data_analysis": {"messages": [msg]}}, step=2)
    assert len(events) == 1
    assert events[0].type == "action"
    assert events[0].tool == "get_sensor_logs"
    assert events[0].tool_input == {"line_name": "B-Line"}


def test_tool_node_maps_to_observation():
    obs = ToolMessage(content="EQP-003 65도", tool_call_id="c1", name="get_sensor_logs")
    events = map_updates_chunk({"data_analysis_tools": {"messages": [obs]}}, step=3)
    assert len(events) == 1
    assert events[0].type == "observation"
    assert events[0].tool == "get_sensor_logs"


def test_finalizer_tokens_map_to_answer():
    chunk = (AIMessage(content="EQP-003에서 "), {"langgraph_node": "finalizer"})
    events = map_messages_chunk(chunk)
    assert events[0].type == "answer"
    assert events[0].delta == "EQP-003에서 "


def test_non_finalizer_tokens_ignored():
    chunk = (AIMessage(content="라우팅 중"), {"langgraph_node": "supervisor"})
    assert map_messages_chunk(chunk) == []


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

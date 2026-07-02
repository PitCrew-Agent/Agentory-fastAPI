"""SSE 이벤트 스키마 계약 테스트

백엔드 이벤트의 agentory.common.events 계약 준수 검증
프론트는 docs/sse-events.md의 동일 계약 기준으로 파싱
"""

import pytest

from agentory.common.events import sse_event_adapter

VALID_PAYLOADS = [
    {"type": "thought", "step": 1, "agent": "supervisor", "content": "B라인 로그부터 수집한다"},
    {
        "type": "action",
        "step": 1,
        "agent": "data_analysis",
        "tool": "get_sensor_logs",
        "tool_input": {"line_name": "B-Line"},
        "reason": "이상 설비 특정을 위해 최근 센서 로그 필요",
    },
    {
        "type": "observation",
        "step": 1,
        "agent": "data_analysis",
        "tool": "get_sensor_logs",
        "content": [{"equipment_id": "EQP-003", "temperature": 65.0}],
    },
    {"type": "answer", "delta": "EQP-003에서 "},
    {"type": "error", "code": "EXTERNAL_SERVICE_ERROR", "message": "LLM 호출 실패"},
    {
        "type": "done",
        "citations": [{"doc_id": "MAN-ETC-042", "data_as_of": "2026-07-02T10:00:00+09:00"}],
        "grounded": True,
    },
]


@pytest.mark.parametrize("payload", VALID_PAYLOADS, ids=lambda p: p["type"])
def test_valid_event_parses(payload):
    event = sse_event_adapter.validate_python(payload)
    assert event.type == payload["type"]


def test_unknown_event_type_rejected():
    with pytest.raises(ValueError):
        sse_event_adapter.validate_python({"type": "unknown", "data": 1})

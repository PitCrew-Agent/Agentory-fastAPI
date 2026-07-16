"""SSE 이벤트 스키마 계약 테스트

백엔드 이벤트의 agentory.common.events 계약 준수 검증
프론트는 docs/sse-events.md의 동일 계약 기준으로 파싱
"""

import pytest

from agentory.common.events import notification_event_adapter, sse_event_adapter

VALID_PAYLOADS = [
    {"type": "thought", "step": 1, "agent": "supervisor", "content": "B라인 로그부터 수집한다"},
    {
        "type": "action",
        "step": 1,
        "agent": "data_analysis",
        "tool": "get_sensor_logs",
        "tool_input": {"line_name": "B라인"},
        "reason": "이상 설비 특정을 위해 최근 센서 로그 필요",
    },
    {
        "type": "observation",
        "step": 1,
        "agent": "data_analysis",
        "tool": "get_sensor_logs",
        "content": [{"equipment_id": "EQP-003", "temperature": 65.0}],
    },
    {
        "type": "action",
        "step": 2,
        "agent": "maintenance",
        "tool": "get_repair_history",
        "tool_input": {"equipment_id": "EQP-A05"},
        "reason": "과거 동일 알람 수리 이력 확인",
    },
    {"type": "answer", "delta": "EQP-003에서 "},
    {"type": "error", "code": "EXTERNAL_SERVICE_ERROR", "message": "LLM 호출 실패"},
    {
        "type": "done",
        "citations": [{"doc_id": "MAN-ETC-042", "data_as_of": "2026-07-02T10:00:00+09:00"}],
        "grounded": True,
        "suggested_questions": [
            "ERR-402 원인이 뭐야?",
            "EQP-002 조치 방법 알려줘",
            "유사 사례가 있었어?",
        ],
    },
]


@pytest.mark.parametrize("payload", VALID_PAYLOADS, ids=lambda p: p["type"])
def test_valid_event_parses(payload):
    event = sse_event_adapter.validate_python(payload)
    assert event.type == payload["type"]


def test_unknown_event_type_rejected():
    with pytest.raises(ValueError):
        sse_event_adapter.validate_python({"type": "unknown", "data": 1})


# 알림 스트림 이벤트 계약 (NEW_PROACT01_ALERT01), 챗 스트림과 별개 어댑터
NOTIFICATION_PAYLOAD = {
    "type": "notification",
    "id": 42,
    "occurred_at": "2026-07-06T10:02:00+09:00",
    "equipment_id": "EQP-003",
    "metric": "temperature",
    "alarm_code": "ERR-402",
    "severity": "위험",
    "message": "EQP-003 냉각 이상 (온도 상승·압력 하강)",
    "is_read": False,
}


def test_notification_event_parses():
    event = notification_event_adapter.validate_python(NOTIFICATION_PAYLOAD)
    assert event.type == "notification"
    assert event.id == 42
    assert event.metric == "temperature"
    assert event.alarm_code == "ERR-402"
    assert event.severity == "위험"


def test_notification_event_allows_null_metric():
    # 변수 특정 불가(레거시·watcher) 시 metric은 null 허용
    event = notification_event_adapter.validate_python({**NOTIFICATION_PAYLOAD, "metric": None})
    assert event.metric is None


def test_notification_event_requires_fields():
    # 필수 필드 누락 시 거부 (프론트 계약 보증)
    with pytest.raises(ValueError):
        notification_event_adapter.validate_python({"type": "notification", "id": 1})

"""MCP maintenance 시간 파싱·직렬화·요약 집계 단위 테스트 (BE_MCP05_MAINT01)

DB 없이 순수 로직(파라미터 파싱·datetime 직렬화·수리 이력 요약)만 검증
"""

from datetime import UTC, datetime

import pytest

from mcp_maintenance.server import _parse_time, _serialize, _summarize


def test_parse_time_naive_defaults_to_utc():
    dt = _parse_time("2026-06-19T07:00:00", "start_time")
    assert dt.tzinfo == UTC


def test_parse_time_invalid_raises():
    with pytest.raises(ValueError, match="시간 형식 오류"):
        _parse_time("어제", "start_time")


def test_serialize_converts_datetime_to_isoformat():
    # repository dict의 repaired_at(datetime)을 JSON 직렬화 가능한 문자열로 변환
    at = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    row = _serialize({"id": 1, "equipment_id": "EQP-A05", "repaired_at": at})
    assert row["repaired_at"] == at.isoformat()
    assert isinstance(row["repaired_at"], str)


def test_summarize_empty_history():
    summary = _summarize("EQP-A05", [])
    assert summary["repair_count"] == 0
    assert summary["last_repaired_at"] is None
    assert summary["alarm_code_counts"] == {}


def test_summarize_counts_recurring_alarm_codes():
    # 최근순 정렬 전제, 동일 알람 코드 재발을 횟수로 집계하고 최근 수리 시각을 첫 행에서 취함
    latest = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    rows = [
        {"repaired_at": latest, "alarm_code_before": "ERR-401"},
        {"repaired_at": datetime(2026, 6, 18, 9, 0, tzinfo=UTC), "alarm_code_before": "ERR-401"},
        {"repaired_at": datetime(2026, 5, 1, 8, 0, tzinfo=UTC), "alarm_code_before": None},
    ]
    summary = _summarize("EQP-A05", rows)
    assert summary["repair_count"] == 3
    assert summary["last_repaired_at"] == latest.isoformat()
    # ERR-401 2회 재발, alarm_code_before None은 집계 제외
    assert summary["alarm_code_counts"] == {"ERR-401": 2}

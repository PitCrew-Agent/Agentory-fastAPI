"""MCP realtime 시간 파싱·검증 단위 테스트 (BE_MCP02_TELEMETRY01)

DB 없이 파라미터 파싱 로직만 검증
"""

from datetime import UTC

import pytest

from mcp_realtime.server import _parse_time


def test_parse_time_with_timezone():
    dt = _parse_time("2026-06-19T07:00:00+09:00", "start_time")
    assert dt.tzinfo is not None


def test_parse_time_naive_defaults_to_utc():
    dt = _parse_time("2026-06-19T07:00:00", "start_time")
    assert dt.tzinfo == UTC


def test_parse_time_invalid_raises():
    with pytest.raises(ValueError, match="시간 형식 오류"):
        _parse_time("어제", "start_time")

"""알림 커서 인코딩 유닛 테스트 (NEW_PROACT01_ALERT02)

DB 불필요, 커서 왕복·오류 처리만 검증
"""

from datetime import UTC, datetime

import pytest

from agentory.modules.notification.service import _decode_cursor, _encode_cursor


def test_cursor_roundtrip_preserves_time_and_id():
    dt = datetime(2026, 7, 10, 3, 4, 5, 123456, tzinfo=UTC)
    assert _decode_cursor(_encode_cursor(dt, 42)) == (dt, 42)


def test_decode_invalid_base64_raises():
    with pytest.raises(ValueError):
        _decode_cursor("!!!not-base64!!!")


def test_decode_missing_id_delimiter_raises():
    # base64("foobar")에는 구분자 '|'가 없어 파싱 실패
    import base64

    bad = base64.urlsafe_b64encode(b"foobar").decode()
    with pytest.raises(ValueError):
        _decode_cursor(bad)

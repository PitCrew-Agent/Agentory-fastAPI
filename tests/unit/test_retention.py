"""조회 가능 구간 산출·검증 단위 테스트 (BE_CHAT02_SUGGEST02)

추천 질문이 실제 보유하지 않은 기간을 요청하지 않도록 후처리 필터 동작을 고정
"""

import pytest

from agentory.modules.agent import retention


@pytest.fixture(autouse=True)
def fixed_window(monkeypatch):
    # 설정 변동과 무관하게 41분(500행 x 5초) 구간으로 고정
    monkeypatch.setattr(retention, "available_window_minutes", lambda: 41.0)


@pytest.mark.parametrize(
    "text",
    [
        "EQP-002 온도 30일 추세 보여줘",
        "24시간 추세 보여줘",
        "최근 7일 알람 이력 확인해줘",
        "일주일 알람 이력 확인해줘",
        "지난달 데이터 비교해줘",
        "3개월 추이 알려줘",
    ],
)
def test_rejects_period_beyond_window(text):
    # 조회 가능 구간을 넘는 기간 언급은 폐기
    assert retention.within_available_window(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "최근 알람 이력 확인해줘",
        "같은 A라인 다른 설비와 비교해줘",
        "최근 10분 온도 확인해줘",
        "ERR-401 관련 매뉴얼 찾아줘",
        "담당자에게 조치 요청해줘",
    ],
)
def test_allows_period_within_window(text):
    # 기간 미언급 또는 구간 안 기간은 통과
    assert retention.within_available_window(text) is True


def test_maintenance_history_exempt_from_window():
    # 정비·수리 이력은 센서 구간 제한과 무관한 별도 테이블이라 통과
    assert retention.within_available_window("지난 6개월 수리 이력 알려줘") is True


def test_window_minutes_follows_settings(monkeypatch):
    # 행수 상한 x 수집 주기로 산출
    monkeypatch.undo()
    settings = retention.get_settings()
    expected = settings.sensor_log_max_rows * settings.sensor_sampling_interval_seconds / 60
    assert retention.available_window_minutes() == pytest.approx(expected)


def test_format_window_uses_minutes_under_an_hour(monkeypatch):
    # 60분 미만이면 분 단위 표기
    monkeypatch.setattr(retention, "available_window_minutes", lambda: 41.0)
    assert retention.format_available_window() == "약 41분"

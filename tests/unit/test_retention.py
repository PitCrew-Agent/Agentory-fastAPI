"""보유 범위 산출·기간 검증 단위 테스트 (BE_CHAT02_SUGGEST03)

추천 질문이 실제 적재 범위를 벗어난 기간을 요청하지 않도록 후처리 필터 동작을 고정
행수 상한이 아니라 실제 데이터 범위가 기준임을 회귀로 보장 (#191)
"""

from datetime import UTC, datetime

import pytest

from agentory.modules.agent.retention import (
    DataWindow,
    format_data_window,
    within_data_window,
)

# 8일치 적재를 가정한 보유 범위 (dev 실측과 동일 형태)
WINDOW_8D = DataWindow(
    first=datetime(2026, 7, 8, 0, 0, tzinfo=UTC),
    last=datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
)
WINDOW_EMPTY = DataWindow(first=None, last=None)


def test_span_days_from_actual_range():
    # 보유 기간은 최초·최근 시각의 차이
    assert WINDOW_8D.span_days == pytest.approx(8.0)
    assert WINDOW_EMPTY.span_days == 0.0


@pytest.mark.parametrize(
    "text",
    [
        "최근 2일 알람 이력 확인해줘",
        "이번주 온도 추세 보여줘",
        "최근 24시간 알람 확인해줘",
        "최근 7일 센서 로그 비교해줘",
        "최근 10분 온도 확인해줘",
        "일주일 알람 이력 확인해줘",
    ],
)
def test_allows_period_within_actual_range(text):
    # 보유 범위(8일) 안의 기간은 통과, 행수 상한 기준일 때 오폐기되던 케이스 회귀
    assert within_data_window(text, WINDOW_8D) is True


@pytest.mark.parametrize(
    "text",
    [
        "EQP-002 온도 30일 추세 보여줘",
        "최근 3개월 추이 알려줘",
        "지난달 데이터 비교해줘",
        "1년 추세 보여줘",
    ],
)
def test_rejects_period_beyond_actual_range(text):
    # 보유 범위를 넘는 기간은 폐기
    assert within_data_window(text, WINDOW_8D) is False


@pytest.mark.parametrize(
    "text",
    [
        "최근 알람 이력 확인해줘",
        "같은 A라인 다른 설비와 비교해줘",
        "ERR-401 관련 매뉴얼 찾아줘",
        "담당자에게 조치 요청해줘",
    ],
)
def test_allows_text_without_period(text):
    # 기간 언급이 없으면 통과
    assert within_data_window(text, WINDOW_8D) is True


def test_maintenance_history_exempt_from_window():
    # 정비·수리 이력은 텔레메트리 보유 범위와 무관한 별도 테이블이라 통과
    assert within_data_window("지난 6개월 수리 이력 알려줘", WINDOW_8D) is True


def test_rejects_all_periods_when_no_data():
    # 적재 데이터가 없으면 기간을 특정한 요청은 답할 수 없음
    assert within_data_window("최근 2일 알람 확인해줘", WINDOW_EMPTY) is False
    assert within_data_window("최근 알람 확인해줘", WINDOW_EMPTY) is True


def test_format_window_shows_range_and_length():
    # 프롬프트 주입 표기는 실제 날짜 범위와 길이를 함께 노출
    assert (
        format_data_window(WINDOW_8D)
        == "2026-07-08T00:00:00+00:00 ~ 2026-07-16T00:00:00+00:00 (약 8일)"
    )
    assert format_data_window(WINDOW_EMPTY) == "적재된 센서 데이터 없음"


def test_format_window_uses_hours_under_a_day():
    # 하루 미만이면 시간 단위 표기
    window = DataWindow(
        first=datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
        last=datetime(2026, 7, 16, 6, 0, tzinfo=UTC),
    )
    assert (
        format_data_window(window)
        == "2026-07-16T00:00:00+00:00 ~ 2026-07-16T06:00:00+00:00 (약 6시간)"
    )

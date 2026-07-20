"""조회 가능 데이터 구간 산출·검증 (BE_CHAT02_SUGGEST02)

센서·알람 데이터는 수집 주기와 행수 상한(sensor_log_max_rows)으로 사실상 구간이 제한됨
추천 질문 생성 시 이 구간을 프롬프트로 주입하고, 구간을 넘는 기간을 언급한 추천은 후처리에서 폐기
"""

import re

from agentory.core.config import get_settings

# 기간 표현 추출용, 숫자 + 단위 조합
_PERIOD_PATTERN = re.compile(r"(\d+)\s*(분|시간|일|주일|주|개월|달|년)")

# 단위별 분 환산값
_UNIT_MINUTES: dict[str, float] = {
    "분": 1,
    "시간": 60,
    "일": 60 * 24,
    "주": 60 * 24 * 7,
    "주일": 60 * 24 * 7,
    "개월": 60 * 24 * 30,
    "달": 60 * 24 * 30,
    "년": 60 * 24 * 365,
}

# 숫자 없이 장기 구간을 뜻하는 표현
_LONG_RANGE_WORDS = (
    "한달",
    "한 달",
    "지난달",
    "지난 달",
    "이번달",
    "이번 달",
    "일주일",
    "한주",
    "한 주",
    "지난주",
    "지난 주",
    "장기",
    "월간",
    "주간",
    "연간",
    "며칠",
)

# 정비 이력은 센서 구간 제한과 무관한 별도 테이블이라 기간 검증 대상에서 제외
_MAINTENANCE_HINT = re.compile(r"정비|수리|교체")


def available_window_minutes() -> float:
    # 설비당 실제 조회 가능한 최근 구간(분), 행수 상한 x 수집 주기
    settings = get_settings()
    return settings.sensor_log_max_rows * settings.sensor_sampling_interval_seconds / 60


def format_available_window() -> str:
    # 프롬프트 주입용 구간 표기, 60분 미만이면 분 단위로 표기
    minutes = available_window_minutes()
    if minutes < 60:
        return f"약 {int(minutes)}분"
    hours = minutes / 60
    if hours < 24:
        return f"약 {hours:.0f}시간"
    return f"약 {hours / 24:.0f}일"


def _mentioned_minutes(text: str) -> list[float]:
    # 문장에 언급된 기간 표현을 분 단위로 환산
    values = [int(amount) * _UNIT_MINUTES[unit] for amount, unit in _PERIOD_PATTERN.findall(text)]
    if any(word in text for word in _LONG_RANGE_WORDS):
        values.append(_UNIT_MINUTES["주"])
    return values


def within_available_window(text: str) -> bool:
    # 언급된 기간이 조회 가능 구간 안인지 검사, 기간 언급이 없으면 통과
    # 정비·수리 이력 요청은 센서 구간 제한과 무관해 통과
    if _MAINTENANCE_HINT.search(text):
        return True
    limit = available_window_minutes()
    return all(value <= limit for value in _mentioned_minutes(text))

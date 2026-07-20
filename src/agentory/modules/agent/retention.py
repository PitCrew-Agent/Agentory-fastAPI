"""조회 가능 데이터 범위 산출·검증 (BE_MCP02_TELEMETRY03, BE_CHAT02_SUGGEST03)

실제 적재된 텔레메트리의 최초·최근 시각을 보유 범위로 삼음
행수 상한(sensor_log_max_rows)은 1회 조회 반환량 제한이지 보유 기간이 아니므로 기준에서 제외
추천 질문 생성 시 이 범위를 프롬프트로 주입하고, 범위를 넘는 기간을 언급한 추천은 후처리에서 폐기
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from agentory.core.db import SessionLocal
from agentory.modules.telemetry.models import EquipmentTelemetry

log = logging.getLogger(__name__)

# 보유 범위 캐시 수명(초), 적재가 계속되므로 주기적으로 갱신
_CACHE_TTL_SECONDS = 300.0

# 기간 표현 추출용, 숫자 + 단위 조합
_PERIOD_PATTERN = re.compile(r"(\d+)\s*(분|시간|일|주일|주|개월|달|년)")

# 단위별 일 환산값
_UNIT_DAYS: dict[str, float] = {
    "분": 1 / 1440,
    "시간": 1 / 24,
    "일": 1,
    "주": 7,
    "주일": 7,
    "개월": 30,
    "달": 30,
    "년": 365,
}

# 숫자 없이 기간을 뜻하는 표현, 일 단위 환산
_RELATIVE_DAYS: dict[str, float] = {
    "이번주": 7,
    "이번 주": 7,
    "지난주": 7,
    "지난 주": 7,
    "한주": 7,
    "한 주": 7,
    "일주일": 7,
    "주간": 7,
    "이번달": 30,
    "이번 달": 30,
    "지난달": 30,
    "지난 달": 30,
    "한달": 30,
    "한 달": 30,
    "월간": 30,
    "연간": 365,
}

# 정비 이력은 텔레메트리 보유 범위와 무관한 별도 테이블이라 기간 검증 대상에서 제외
_MAINTENANCE_HINT = re.compile(r"정비|수리|교체")


@dataclass(frozen=True)
class DataWindow:
    """텔레메트리 보유 범위

    적재 데이터가 없으면 first·last가 None이고 known은 True (조회는 성공)
    조회 자체가 실패하면 known이 False, 이 경우 기간 검증을 적용하지 않음
    """

    first: datetime | None
    last: datetime | None
    known: bool = True

    @property
    def span_days(self) -> float:
        # 보유 기간(일), 데이터 없거나 조회 실패면 0
        if self.first is None or self.last is None:
            return 0.0
        return (self.last - self.first).total_seconds() / 86400


_cache: tuple[DataWindow, float] | None = None


async def get_data_window(*, refresh: bool = False) -> DataWindow:
    # 적재된 텔레메트리의 최초·최근 시각, TTL 동안 캐시해 추천 생성마다 집계하지 않음
    # 조회 실패는 답변 생성을 막지 않고 미상(known=False)으로 격리, 기간 검증만 건너뜀
    global _cache
    now = time.monotonic()
    if not refresh and _cache is not None and _cache[1] > now:
        return _cache[0]
    try:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(
                        func.min(EquipmentTelemetry.timestamp),
                        func.max(EquipmentTelemetry.timestamp),
                    )
                )
            ).one()
        window = DataWindow(first=row[0], last=row[1])
    except Exception as exc:
        log.warning("[retention] 보유 범위 조회 실패, 기간 검증 생략: %s", exc)
        window = DataWindow(first=None, last=None, known=False)
    _cache = (window, now + _CACHE_TTL_SECONDS)
    return window


def format_data_window(window: DataWindow) -> str:
    # 프롬프트 주입용 범위 표기, "최근 N" 앵커로 쓰이므로 초 단위 시각까지 노출
    # 분 단위로 절삭하면 마지막 몇 초의 행이 조회 범위 밖으로 밀려나므로 초까지 유지
    if not window.known:
        return "보유 범위 확인 불가 (조회 실패)"
    if window.first is None or window.last is None:
        return "적재된 센서 데이터 없음"
    span = window.span_days
    if span < 1:
        length = f"약 {span * 24:.0f}시간"
    else:
        length = f"약 {span:.0f}일"
    first = window.first.isoformat(timespec="seconds")
    last = window.last.isoformat(timespec="seconds")
    return f"{first} ~ {last} ({length})"


def _mentioned_days(text: str) -> list[float]:
    # 문장에 언급된 기간 표현을 일 단위로 환산
    values = [int(amount) * _UNIT_DAYS[unit] for amount, unit in _PERIOD_PATTERN.findall(text)]
    values += [days for word, days in _RELATIVE_DAYS.items() if word in text]
    return values


def within_data_window(text: str, window: DataWindow) -> bool:
    # 언급된 기간이 보유 범위 안인지 검사, 기간 언급이 없으면 통과
    # 정비·수리 이력 요청은 텔레메트리 보유 범위와 무관해 통과
    # 보유 범위를 모르면 검증을 적용하지 않음, 조회 장애가 추천 전면 차단으로 번지지 않도록 함
    if not window.known:
        return True
    if _MAINTENANCE_HINT.search(text):
        return True
    mentioned = _mentioned_days(text)
    if not mentioned:
        return True
    span = window.span_days
    if span <= 0:
        # 데이터가 없으면 기간을 특정한 요청은 모두 답할 수 없음
        return False
    return all(value <= span for value in mentioned)

"""섀도우 vs 규칙 레이어 비교 리포트 (BE_ANOM01_SHADOW01)

실행: uv run anomaly-shadow-report [--days N]
섀도우 관찰과 동시점 규칙 알람을 설비·시간 기준으로 비교해 감지 격차를 분류
- detector-only: 섀도우만 발생 (규칙이 놓친 임계 안쪽 이상 후보)
- overlapping: 섀도우·규칙 시간 겹침 (동일 이상을 양쪽이 감지)
- rule-only: 규칙만 발생 (섀도우 미감지, 급성 등)
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.db import SessionLocal
from agentory.core.logging import setup_logging
from agentory.modules.telemetry.models import EquipmentAlarm
from agentory.modules.watcher.detector import ANOMALY_ALARM_CODE
from agentory.modules.watcher.models import EquipmentAnomalyShadowEvent

log = logging.getLogger("anomaly-shadow-report")


@dataclass
class Interval:
    equipment_id: str
    start: datetime
    end: datetime


def _overlaps(a: Interval, b: Interval) -> bool:
    return a.equipment_id == b.equipment_id and a.start <= b.end and b.start <= a.end


async def _shadow_intervals(
    session: AsyncSession, since: datetime, now: datetime
) -> list[Interval]:
    rows = await session.execute(
        select(
            EquipmentAnomalyShadowEvent.equipment_id,
            EquipmentAnomalyShadowEvent.raised_at,
            EquipmentAnomalyShadowEvent.cleared_at,
        ).where(EquipmentAnomalyShadowEvent.raised_at >= since)
    )
    return [Interval(eid, raised, cleared or now) for eid, raised, cleared in rows]


async def _rule_intervals(session: AsyncSession, since: datetime, now: datetime) -> list[Interval]:
    # 규칙 레이어 알람만 (WRN-901 이상 감지 코드 제외)
    rows = await session.execute(
        select(
            EquipmentAlarm.equipment_id,
            EquipmentAlarm.raised_at,
            EquipmentAlarm.cleared_at,
        ).where(
            EquipmentAlarm.raised_at >= since,
            EquipmentAlarm.alarm_code != ANOMALY_ALARM_CODE,
        )
    )
    return [Interval(eid, raised, cleared or now) for eid, raised, cleared in rows]


def compare(shadow: list[Interval], rule: list[Interval]) -> dict[str, int]:
    """섀도우·규칙 구간 비교 분류 집계"""
    detector_only = sum(1 for s in shadow if not any(_overlaps(s, r) for r in rule))
    overlapping = sum(1 for s in shadow if any(_overlaps(s, r) for r in rule))
    rule_only = sum(1 for r in rule if not any(_overlaps(r, s) for s in shadow))
    return {
        "shadow_total": len(shadow),
        "rule_total": len(rule),
        "detector_only": detector_only,
        "overlapping": overlapping,
        "rule_only": rule_only,
    }


async def run_report(days: int) -> dict[str, int]:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    async with SessionLocal() as session:
        shadow = await _shadow_intervals(session, since, now)
        rule = await _rule_intervals(session, since, now)
    return compare(shadow, rule)


def run() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="섀도우 vs 규칙 비교 리포트")
    parser.add_argument("--days", type=int, default=7, help="비교 기간(일)")
    args = parser.parse_args()
    summary = asyncio.run(run_report(args.days))
    log.info("[shadow-report] 최근 %d일 비교", args.days)
    log.info(
        "[shadow-report] 섀도우 %d건 / 규칙 %d건", summary["shadow_total"], summary["rule_total"]
    )
    log.info("[shadow-report] detector-only(규칙 놓침 후보): %d", summary["detector_only"])
    log.info("[shadow-report] overlapping(양쪽 감지): %d", summary["overlapping"])
    log.info("[shadow-report] rule-only(섀도우 미감지): %d", summary["rule_only"])

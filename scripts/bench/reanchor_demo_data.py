"""데모 텔레메트리·알람 시각을 현재로 재정렬 (AI_AGENT01_QUALITY01)

시드(scripts/seed_data.py)는 텔레메트리를 고정 시각(2026-07-08)에 앵커하지만, 평가 실행 시점이
그보다 뒤면 에이전트의 "현재 시각 기준 최근 N시간" 조회가 빈 결과가 되어 답변 품질 비교가
데이터 신선도 아티팩트에 오염된다. 이 스크립트는 라이브 시뮬레이터가 현재까지 적재한 상태를
재현하도록 텔레메트리 timestamp와 알람 raised_at·cleared_at을 동일 delta만큼 평행 이동해
최신 tick이 "지금"에 오도록 맞춘다. 수리 이력(전체 기간 조회)은 건드리지 않는다.

멱등: 실행할 때마다 최신 tick을 다시 현재로 맞추므로 반복 실행해도 안전하다.
실행: uv run python scripts/bench/reanchor_demo_data.py [--buffer-min 2]
전제: DB 시드 완료
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select, update

from agentory.core.db import SessionLocal
from agentory.modules.telemetry.models import EquipmentAlarm, EquipmentTelemetry


async def reanchor(buffer_min: int) -> None:
    async with SessionLocal() as session:
        max_ts = (
            await session.execute(select(func.max(EquipmentTelemetry.timestamp)))
        ).scalar_one_or_none()
        if max_ts is None:
            print("텔레메트리 없음, 재정렬 생략 (시드 먼저 실행)")
            return
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=UTC)

        target = datetime.now(UTC) - timedelta(minutes=buffer_min)
        delta = target - max_ts
        if abs(delta.total_seconds()) < 60:
            print(f"이미 최신 상태(최신 tick={max_ts.isoformat()}), 재정렬 생략")
            return

        # 텔레메트리·알람 시각을 동일 delta만큼 평행 이동, cleared_at NULL은 보존
        await session.execute(
            update(EquipmentTelemetry).values(timestamp=EquipmentTelemetry.timestamp + delta)
        )
        await session.execute(
            update(EquipmentAlarm).values(
                raised_at=EquipmentAlarm.raised_at + delta,
                cleared_at=case(
                    (EquipmentAlarm.cleared_at.is_(None), None),
                    else_=EquipmentAlarm.cleared_at + delta,
                ),
            )
        )
        await session.commit()

        new_max = (
            await session.execute(select(func.max(EquipmentTelemetry.timestamp)))
        ).scalar_one()
        active = (
            await session.execute(
                select(func.count())
                .select_from(EquipmentAlarm)
                .where(EquipmentAlarm.cleared_at.is_(None))
            )
        ).scalar_one()
        print(
            f"재정렬 완료: delta={delta}, 최신 tick={new_max.isoformat()}, "
            f"활성 알람 {active}건 (수리 이력 미변경)"
        )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buffer-min", type=int, default=2, help="최신 tick을 현재보다 N분 이전으로")
    args = ap.parse_args()
    await reanchor(args.buffer_min)


if __name__ == "__main__":
    asyncio.run(main())

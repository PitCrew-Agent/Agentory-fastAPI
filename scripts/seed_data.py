"""설비 마스터·텔레메트리 샘플 데이터 적재 (DEV_DATABASE)

요구사항 정의서 §8.1 / §8.2 샘플 기반, 멱등 실행(존재 시 스킵)
실행: uv run python scripts/seed_data.py
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from agentory.core.db import SessionLocal
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry

EQUIPMENT = [
    ("EQP-001", "A-Line", "Deposition", "Zone-A", "Main-Tech 1"),
    ("EQP-002", "A-Line", "Etching", "Zone-A", "Main-Tech 1"),
    ("EQP-003", "B-Line", "Etching", "Zone-B", "Main-Tech 2"),
]

# §8.2 샘플 기반 EQP-003 이상 추이, (분, 온도, 압력, rf_power, gas_flow, 알람)
# temperature·pressure·alarm_code는 문서 §8.2 원본값
# rf_power(kW)·gas_flow(sccm)는 ERD v2 신규 컬럼이라 문서 근거 없는 합성값
# ERR-402 원인은 냉각수 밸브 압력 저하이므로 공정 파라미터(RF·가스)는 정상 범위 유지로 구성
TELEMETRY_EQP003 = [
    (0, "42.10", "1.20", "2.00", "150.00", None),
    (15, "53.50", "1.15", "2.04", "149.20", "ERR-402"),
    (30, "61.20", "0.98", "1.98", "151.30", "ERR-402"),
    (45, "65.00", "0.85", "2.06", "148.70", "ERR-402"),
]


async def seed() -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(select(EquipmentMaster).limit(1))
        if existing is not None:
            print("[seed] equipment_master 데이터 존재, 스킵")
            return

        session.add_all(
            EquipmentMaster(
                equipment_id=eid,
                line_name=line,
                process_type=proc,
                location=loc,
                manager_dept=dept,
            )
            for eid, line, proc, loc, dept in EQUIPMENT
        )

        base = datetime(2026, 6, 19, 7, 0, 0, tzinfo=UTC)
        for minute, temp, press, rf_power, gas_flow, alarm in TELEMETRY_EQP003:
            session.add(
                EquipmentTelemetry(
                    equipment_id="EQP-003",
                    timestamp=base.replace(minute=minute),
                    temperature=Decimal(temp),
                    pressure=Decimal(press),
                    rf_power=Decimal(rf_power),
                    gas_flow=Decimal(gas_flow),
                    alarm_code=alarm,
                )
            )

        await session.commit()
        print(f"[seed] 설비 {len(EQUIPMENT)}건, 텔레메트리 {len(TELEMETRY_EQP003)}건 적재 완료")


if __name__ == "__main__":
    asyncio.run(seed())

"""설비 마스터·텔레메트리 샘플 데이터 적재 (DEV_DATABASE)

요구사항 정의서 §8.1 / §8.2 샘플 기반, 멱등 실행(존재 시 스킵)
실행: uv run python scripts/seed_data.py
"""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from agentory.core.db import SessionLocal
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry

# (id, line, process, location, 책임 부서, 책임자, 마지막 점검일)
EQUIPMENT = [
    ("EQP-001", "A-Line", "Deposition", "Zone-A", "Main-Tech 1", "정도현", date(2026, 6, 28)),
    ("EQP-002", "A-Line", "Etching", "Zone-A", "Main-Tech 1", "김억산", date(2026, 6, 30)),
    ("EQP-003", "B-Line", "Etching", "Zone-B", "Main-Tech 2", "박승우", date(2026, 6, 25)),
]

# EQP-003 ERR-402(냉각 이상) 추이, (분, 온도°C, 압력mTorr, rf_power kW, gas_flow sccm, 알람)
# 시뮬레이터 참고서 §2 Etching 운영 프로파일 스케일 기준
# 온도가 하드리밋 상단(USL=61.50) 도달 + 압력이 초기 밴드 하단 아래로 하강하면 ERR-402
# RF·가스는 냉각 계통과 무관하므로 정상 밴드 유지
TELEMETRY_EQP003 = [
    (0, "60.05", "42.00", "2.79", "606.00", None),
    (15, "60.90", "41.20", "2.80", "605.00", None),
    (30, "61.60", "39.50", "2.78", "607.00", "ERR-402"),
    (45, "62.40", "38.10", "2.79", "604.00", "ERR-402"),
]


async def seed() -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(select(EquipmentMaster).limit(1))
        if existing is not None:
            print("[seed] equipment_masters 데이터 존재, 스킵")
            return

        session.add_all(
            EquipmentMaster(
                equipment_id=eid,
                line_name=line,
                process_type=proc,
                location=loc,
                manager_dept=dept,
                manager_name=mgr,
                last_inspection_at=inspected,
            )
            for eid, line, proc, loc, dept, mgr, inspected in EQUIPMENT
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

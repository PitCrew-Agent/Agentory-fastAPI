"""식각 3라인 설비 배치·텔레메트리 샘플 적재 (DEV_DATABASE / NEW_TWIN01_SCENE01)

3D 트윈 뷰 배치를 그대로 재현하도록 A·B·C 3개 식각 라인의 설비 배치를 마스터에 심고,
시뮬레이터 생성기(simulator.generate_reading)로 설비별 정상·이상 텔레메트리 시계열을 생성
전 설비 식각 챔버(shape=etch, process_type=Etching), 위치·회전은 bay_zone에서 파생
재실행 시 기존 텔레메트리·마스터를 비우고 다시 적재 (개발용 시드)

실행: uv run python scripts/seed_data.py
"""

import asyncio
import math
import random
from collections import deque
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete

from agentory.core.db import SessionLocal
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from simulator.generator import generate_reading
from simulator.scenarios import SCENARIOS

# bay_zone별 3D 좌표·회전 (설비 문서 값 규칙), north는 정면·south는 180도 회전
BAY_GEOMETRY = {
    "north": {"z": 1.65, "rotation": 0.0},
    "south": {"z": -1.65, "rotation": math.pi},
}

# 식각 공정 단일 운영이라 전 설비 동일, 프론트는 shape로 3D 모델 선택
ETCH_PROCESS = "Etching"
ETCH_SHAPE = "etch"

# 라인 메타 (id, 표시명, 책임 부서, 책임자)
LINES = [
    ("A", "A-Line", "Main-Tech 1", "김억산"),
    ("B", "B-Line", "Main-Tech 2", "정하린"),
    ("C", "C-Line", "Main-Tech 3", "이준호"),
]

# 라인별 설비 배치, 문서 배치의 x·bay_zone·상태 등급을 유지하되 전 슬롯 식각 챔버로 치환
# (display_order, position_x, bay_zone, scenario), scenario는 이상 등급 재현용 시뮬레이터 시나리오
# "normal"은 양호, 드리프트·급성 시나리오는 주의·위험 재현 (현재 상태 모델은 양호/주의/위험 3단계)
LAYOUT: dict[str, list[tuple[int, float, str, str]]] = {
    "A": [
        (1, -4.80, "north", "normal"),
        (2, -4.20, "south", "normal"),
        (3, -2.60, "south", "pressure_drift_pm"),  # 주의(WRN-702)
        (4, -1.00, "north", "normal"),
        (5, 1.50, "north", "err402_temp_rise"),  # 위험(ERR-402)
        (6, 2.70, "south", "normal"),
        (7, 4.45, "north", "normal"),
    ],
    "B": [
        (1, -4.70, "north", "normal"),
        (2, -4.10, "south", "normal"),
        (3, -2.70, "north", "normal"),
        (4, -1.40, "south", "gas_flow_drift_pm"),  # 주의(WRN-704)
        (5, 1.60, "south", "normal"),
        (6, 2.80, "north", "normal"),
        (7, 4.40, "north", "normal"),
    ],
    "C": [
        (1, -4.60, "north", "normal"),
        (2, -3.10, "north", "normal"),
        (3, -1.80, "south", "temperature_drift_pm"),  # 주의(WRN-701)
        (4, -0.25, "north", "normal"),
        (5, 2.30, "south", "normal"),
        (6, 4.00, "south", "normal"),
    ],
}

# 라인별 마지막 점검일 (데모용 분산)
INSPECTED_AT = {"A": date(2026, 7, 2), "B": date(2026, 7, 4), "C": date(2026, 6, 30)}

# 텔레메트리 시계열 파라미터, 유한 tick으로 정상·이상을 결정론적으로 주입
# 드리프트 PM은 정상 밴드 회랑을 소진해야 하므로 이르게 시작해 최신 tick까지 알람 유지
# 급성(err402)은 짧게 상승하는 이상이라 뒤늦게 시작해 최신값이 현실 범위를 벗어나지 않게 분리
SERIES_TICKS = 44  # 설비당 생성 tick 수
SERIES_INTERVAL_MIN = 5  # tick 간격 분
DRIFT_START_TICK = 4  # 드리프트 PM 시나리오 시작 tick
ACUTE_DRIFT_START_TICK = 36  # 급성 시나리오 시작 tick (최신 구간 ERR-402 확정 유지)
ACUTE_SCENARIOS = {"err402_temp_rise"}  # 급성 이상 시나리오
# 마지막 tick이 기준일 근처가 되도록 시작 시각 앵커
SERIES_BASE = datetime(2026, 7, 8, 5, 0, 0, tzinfo=UTC)


def build_masters() -> list[EquipmentMaster]:
    # 3라인 배치를 설비 마스터로 전개, 위치·회전은 bay_zone에서 파생
    masters: list[EquipmentMaster] = []
    for line_code, line_name, dept, owner in LINES:
        for order, pos_x, bay_zone, _scenario in LAYOUT[line_code]:
            geom = BAY_GEOMETRY[bay_zone]
            equipment_id = f"EQP-{line_code}{order:02d}"
            masters.append(
                EquipmentMaster(
                    equipment_id=equipment_id,
                    line_name=line_name,
                    process_type=ETCH_PROCESS,
                    location=f"Zone-{line_code}",
                    manager_dept=dept,
                    manager_name=owner,
                    last_inspection_at=INSPECTED_AT[line_code],
                    display_order=order,
                    shape=ETCH_SHAPE,
                    bay_zone=bay_zone,
                    position_x=pos_x,
                    position_y=0,
                    position_z=geom["z"],
                    rotation_y=geom["rotation"],
                )
            )
    return masters


def build_telemetry(equipment_id: str, scenario_name: str) -> list[EquipmentTelemetry]:
    # 설비 1대의 정상·이상 시계열 생성 (simulator.generate_reading 재사용)
    # 연속 2 tick 지속 규칙으로 대표 알람 확정, 1 tick 스파이크는 보류 (시뮬레이터 §7과 동일)
    scenario = SCENARIOS[scenario_name]
    drift_start = ACUTE_DRIFT_START_TICK if scenario_name in ACUTE_SCENARIOS else DRIFT_START_TICK
    # 설비별 고정 시드로 재현 가능한 잡음 생성
    rng = random.Random(hash(equipment_id) & 0xFFFFFFFF)
    window: deque = deque(maxlen=8)
    prev_candidate: str | None = None
    rows: list[EquipmentTelemetry] = []
    for tick in range(SERIES_TICKS):
        reading = generate_reading(
            equipment_id,
            ETCH_PROCESS,
            scenario=scenario,
            tick=tick,
            drift_start_tick=drift_start,
            history=list(window),
            rng=rng,
        )
        window.append(reading)
        candidate = reading.alarm_code
        confirmed = candidate if candidate is not None and candidate == prev_candidate else None
        prev_candidate = candidate
        rows.append(
            EquipmentTelemetry(
                equipment_id=equipment_id,
                timestamp=SERIES_BASE + timedelta(minutes=SERIES_INTERVAL_MIN * tick),
                temperature=reading.temperature,
                pressure=reading.pressure,
                rf_power=reading.rf_power,
                gas_flow=reading.gas_flow,
                alarm_code=confirmed,
            )
        )
    return rows


async def seed() -> None:
    masters = build_masters()

    telemetry: list[EquipmentTelemetry] = []
    for line_code, *_ in LINES:
        for order, _pos_x, _bay_zone, scenario_name in LAYOUT[line_code]:
            equipment_id = f"EQP-{line_code}{order:02d}"
            telemetry.extend(build_telemetry(equipment_id, scenario_name))

    async with SessionLocal() as session:
        # 개발용 시드라 기존 데이터 비우고 재적재, FK 때문에 텔레메트리 먼저 삭제
        await session.execute(delete(EquipmentTelemetry))
        await session.execute(delete(EquipmentMaster))
        session.add_all(masters)
        session.add_all(telemetry)
        await session.commit()

    print(f"[seed] 설비 {len(masters)}건, 텔레메트리 {len(telemetry)}건 적재 완료")


if __name__ == "__main__":
    asyncio.run(seed())

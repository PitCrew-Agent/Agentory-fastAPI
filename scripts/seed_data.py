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
from agentory.modules.admin.models import EquipmentRepair, Line, UserLine
from agentory.modules.auth import models as _auth_models  # noqa: F401  users FK 대상 등록
from agentory.modules.telemetry.models import (
    EquipmentAlarm,
    EquipmentMaster,
    EquipmentTelemetry,
)
from simulator.generator import VARS, generate_reading, representative
from simulator.scenarios import SCENARIOS


def _severity(alarm_code: str) -> str:
    # 알람 코드 접두로 심각도 판정 (StatusLevel 값 표기), ERR=위험·그 외=주의
    return "위험" if alarm_code.startswith("ERR") else "주의"


# bay_zone별 3D 좌표·회전 (설비 문서 값 규칙), north는 정면·south는 180도 회전
BAY_GEOMETRY = {
    "north": {"z": 1.8, "rotation": 0.0},
    "south": {"z": -1.8, "rotation": math.pi},
}

# 식각 공정 단일 운영이라 전 설비 동일, 프론트는 shape로 3D 모델 선택
ETCH_PROCESS = "Etching"
ETCH_SHAPE = "etch"

# 라인 메타 (id, 표시명, 책임 부서, 책임자)
LINES = [
    ("A", "A라인", "Main-Tech 1", "최진욱"),
    ("B", "B라인", "Main-Tech 2", "정유민"),
    ("C", "C라인", "Main-Tech 3", "이준호"),
]

# 라인별 설비 배치, 문서 배치의 x·bay_zone·상태 등급을 유지하되 전 슬롯 식각 챔버로 치환
# (display_order, position_x, bay_zone, scenario), scenario는 이상 등급 재현용 시뮬레이터 시나리오
# "normal"은 양호, 드리프트·급성 시나리오는 주의·위험 재현 (현재 상태 모델은 양호/주의/위험 3단계)
LAYOUT: dict[str, list[tuple[int, float, str, str]]] = {
    "A": [
        (1, -3.9, "north", "normal"),
        (2, -3.9, "south", "normal"),
        (3, -1.3, "south", "pressure_drift_pm"),  # 주의(WRN-702)
        (4, -1.3, "north", "normal"),
        (5, 1.3, "north", "temp_acute_pressure_drift"),  # 위험(ERR-401)+주의(WRN-702) 동시
        (6, 1.3, "south", "temperature_acute"),  # 위험(ERR-401)
        (7, 3.9, "north", "normal"),
    ],
    "B": [
        (1, -3.9, "north", "normal"),
        (2, -3.9, "south", "rf_power_drift_pm"),  # 주의(WRN-703)
        (3, -1.3, "north", "pressure_acute"),  # 위험(ERR-301)
        (4, -1.3, "south", "gas_flow_drift_pm"),  # 주의(WRN-704)
        (5, 1.3, "south", "normal"),
        (6, 1.3, "north", "rf_power_acute"),  # 위험(ERR-201)
        (7, 3.9, "north", "normal"),
    ],
    "C": [
        (1, -2.6, "north", "normal"),
        (2, 0.0, "north", "gas_flow_acute"),  # 주의(WRN-501)
        (3, -2.6, "south", "temperature_drift_pm"),  # 주의(WRN-701)
        (4, 2.6, "north", "variance_increase"),  # 주의(WRN-801)
        (5, 0.0, "south", "rf_acute_gas_drift"),  # 위험(ERR-201)+주의(WRN-704) 동시
        (6, 2.6, "south", "normal"),
    ],
}

# 라인별 마지막 점검일 (데모용 분산)
INSPECTED_AT = {"A": date(2026, 7, 2), "B": date(2026, 7, 4), "C": date(2026, 6, 30)}

# 텔레메트리 시계열 파라미터, 유한 tick으로 정상·이상을 결정론적으로 주입
# 드리프트 PM·급성 단일변수·변동성은 이르게 시작해 최신 tick까지 알람 유지
# 급성은 즉시 계단 이탈 + 생성기 클램프라 별도 지연 시작 불필요
SERIES_TICKS = 44  # 설비당 생성 tick 수
SERIES_INTERVAL_MIN = 5  # tick 간격 분
DRIFT_START_TICK = 4  # 드리프트·급성·변동성 시나리오 시작 tick
LATE_START_TICK = 36  # 지연 시작 tick (필요 시 최신 구간만 이탈시킬 시나리오용)
LATE_START_SCENARIOS: set[str] = set()  # 지연 시작 대상 시나리오, 현재 없음
# 마지막 tick이 기준일 근처가 되도록 시작 시각 앵커
SERIES_BASE = datetime(2026, 7, 8, 5, 0, 0, tzinfo=UTC)


def build_lines() -> list[Line]:
    # 라인 마스터, code는 설비 line_name과 매칭되어 담당 라인 스코핑 기준이 됨 (BE_NOTI01_SCOPE01)
    return [
        Line(code=line_name, name=line_name, display_order=order, status="active")
        for order, (_code, line_name, _dept, _owner) in enumerate(LINES, start=1)
    ]


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


def build_telemetry(
    equipment_id: str, scenario_name: str
) -> tuple[list[EquipmentTelemetry], list[EquipmentAlarm]]:
    # 설비 1대의 정상·이상 시계열 + 변수별 알람 이벤트 생성 (simulator.generate_reading 재사용)
    # 변수별 연속 2 tick 지속 규칙으로 확정, 대표 alarm_code는 확정 변수 중 최고 심각도
    # 확정 코드의 발생/해제 전이를 equipment_alarms 이벤트로 산출 (시뮬레이터 적재와 동일 규칙)
    scenario = SCENARIOS[scenario_name]
    drift_start = LATE_START_TICK if scenario_name in LATE_START_SCENARIOS else DRIFT_START_TICK
    # 설비별 고정 시드로 재현 가능한 잡음 생성
    rng = random.Random(hash(equipment_id) & 0xFFFFFFFF)
    window: deque = deque(maxlen=8)
    prev_candidates: dict[str, str | None] = {}
    active: dict[str, tuple[str, datetime]] = {}  # metric -> (code, raised_at) 열린 알람
    rows: list[EquipmentTelemetry] = []
    alarms: list[EquipmentAlarm] = []
    for tick in range(SERIES_TICKS):
        ts = SERIES_BASE + timedelta(minutes=SERIES_INTERVAL_MIN * tick)
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
        # 변수별 확정 (직전 tick과 동일 후보만)
        confirmed: dict[str, str | None] = {}
        for var in VARS:
            cand = reading.alarm_codes.get(var)
            confirmed[var] = cand if cand is not None and cand == prev_candidates.get(var) else None
        prev_candidates = dict(reading.alarm_codes)
        # 변수별 발생/해제 전이 → equipment_alarms 이벤트
        for metric in VARS:
            code = confirmed[metric]
            current = active.get(metric)
            current_code = current[0] if current else None
            if code == current_code:
                continue
            if current is not None:
                alarms.append(
                    EquipmentAlarm(
                        equipment_id=equipment_id,
                        metric=metric,
                        alarm_code=current[0],
                        severity=_severity(current[0]),
                        raised_at=current[1],
                        cleared_at=ts,
                    )
                )
                active.pop(metric)
            if code is not None:
                active[metric] = (code, ts)
        rows.append(
            EquipmentTelemetry(
                equipment_id=equipment_id,
                timestamp=ts,
                temperature=reading.temperature,
                pressure=reading.pressure,
                rf_power=reading.rf_power,
                gas_flow=reading.gas_flow,
                alarm_code=representative(confirmed),
            )
        )
    # 마지막까지 열려 있는 알람은 활성(cleared_at NULL)으로 남겨 현재 알람 상태 재현
    for metric, (code, raised_at) in active.items():
        alarms.append(
            EquipmentAlarm(
                equipment_id=equipment_id,
                metric=metric,
                alarm_code=code,
                severity=_severity(code),
                raised_at=raised_at,
                cleared_at=None,
            )
        )
    return rows, alarms


# 데모용 수리 이력 (BE_MCP05_MAINT01), 일부는 동일 알람 재발로 반복 고장 패턴 재현
# (equipment_id, repaired_at, alarm_code_before, note)
REPAIRS: list[tuple[str, datetime, str, str]] = [
    (
        "EQP-A05",
        datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
        "ERR-401",
        "온도 급상승, 냉각 밸브 점검·교체",
    ),
    (
        "EQP-A05",
        datetime(2026, 7, 2, 14, 0, tzinfo=UTC),
        "ERR-401",
        "동일 증상 재발, 냉각수 라인 세정",
    ),
    ("EQP-B03", datetime(2026, 6, 25, 11, 0, tzinfo=UTC), "ERR-301", "압력 이상, 배관 누설 보수"),
    (
        "EQP-B06",
        datetime(2026, 6, 30, 16, 0, tzinfo=UTC),
        "ERR-201",
        "RF 파워 이상, 매칭 네트워크 조정",
    ),
]


def build_repairs() -> list[EquipmentRepair]:
    # 데모 수리 이력, 책임자 유저는 시드 범위 밖이라 repaired_by 미지정(NULL)
    return [
        EquipmentRepair(
            equipment_id=equipment_id,
            repaired_by=None,
            repaired_at=repaired_at,
            alarm_code_before=alarm_code,
            note=note,
        )
        for equipment_id, repaired_at, alarm_code, note in REPAIRS
    ]


async def seed() -> None:
    masters = build_masters()

    telemetry: list[EquipmentTelemetry] = []
    alarms: list[EquipmentAlarm] = []
    for line_code, *_ in LINES:
        for order, _pos_x, _bay_zone, scenario_name in LAYOUT[line_code]:
            equipment_id = f"EQP-{line_code}{order:02d}"
            rows, alarm_rows = build_telemetry(equipment_id, scenario_name)
            telemetry.extend(rows)
            alarms.extend(alarm_rows)

    lines = build_lines()
    repairs = build_repairs()

    async with SessionLocal() as session:
        # 개발용 시드라 기존 데이터 비우고 재적재, FK 때문에 자식 테이블 먼저 삭제
        await session.execute(delete(EquipmentAlarm))
        await session.execute(delete(EquipmentTelemetry))
        await session.execute(delete(EquipmentRepair))
        await session.execute(delete(EquipmentMaster))
        # 라인 마스터 재적재, 배정(user_lines)은 라인 FK라 함께 비우고 운영 화면에서 재배정
        await session.execute(delete(UserLine))
        await session.execute(delete(Line))
        session.add_all(lines)
        session.add_all(masters)
        await session.flush()  # 마스터 선적재로 자식(telemetry·alarms·repairs) FK 보장
        session.add_all(telemetry)
        session.add_all(alarms)
        session.add_all(repairs)
        await session.commit()

    print(
        f"[seed] 라인 {len(lines)}건, 설비 {len(masters)}건, 텔레메트리 {len(telemetry)}건, "
        f"알람 이벤트 {len(alarms)}건, 수리 이력 {len(repairs)}건 적재 완료"
    )
    print("[seed] 담당 라인 배정은 비어 있음, 운영 화면에서 사용자별 라인을 배정해야 알림이 표시됨")


if __name__ == "__main__":
    asyncio.run(seed())

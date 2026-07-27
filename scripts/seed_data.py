"""식각 3라인 설비 배치·텔레메트리 샘플 적재 (DEV_DATABASE / NEW_TWIN01_SCENE01)

3D 트윈 뷰 배치를 그대로 재현하도록 A·B·C 3개 식각 라인의 설비 배치를 마스터에 심고,
시뮬레이터 생성기(simulator.generate_reading)로 설비별 정상·이상 텔레메트리 시계열을 생성
전 설비 식각 챔버(shape=etch, process_type=Etching), 위치·회전은 bay_zone에서 파생
재실행 시 데모 데이터(텔레메트리·알람·수리 이력)를 비우고 다시 적재하며, 설비 마스터는 삭제 대신
equipment_id 기준 UPSERT로 정의만 갱신해 학습성 이상감지 데이터(calibration·shadow)를 보존 (#219)

실행 전 시뮬레이터 서비스를 중지해야 텔레메트리 삭제와 재적재 사이 신규 유입이 없습니다
실행: uv run python scripts/seed_data.py
"""

import asyncio
import math
import random
import zlib
from collections import deque
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agentory.core.db import SessionLocal
from agentory.modules.admin.models import EquipmentRepair, Line, UserLine
from agentory.modules.auth.models import User
from agentory.modules.telemetry.models import (
    EquipmentAlarm,
    EquipmentMaster,
    EquipmentTelemetry,
)
from agentory.modules.telemetry.schemas import alarm_severity
from simulator.generator import VARS, generate_reading, representative, surface_codes
from simulator.scenarios import PRESETS, SCENARIOS


def _severity(alarm_code: str) -> str:
    # 심각도 판정은 telemetry 단일 소스 사용 (복합·다변량만 위험, 단일 밴드 이탈은 주의)
    return alarm_severity(alarm_code)


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
    ("A", "A라인", "Main-Tech 1", "하경훈"),
    ("B", "B라인", "Main-Tech 2", "김철용"),
    ("C", "C라인", "Main-Tech 3", "이준호"),
]

# 라인별 설비 3D 배치 (display_order, position_x, bay_zone), 문서 배치의 x·bay_zone 유지
# 이상 시나리오 배치는 PRESETS["floor_demo"]가 단일 소스 (시드·라이브 공유)
LAYOUT: dict[str, list[tuple[int, float, str]]] = {
    "A": [
        (1, -3.9, "north"),
        (2, -3.9, "south"),
        (3, -1.3, "south"),
        (4, -1.3, "north"),
        (5, 1.3, "north"),
        (6, 1.3, "south"),
        (7, 3.9, "north"),
    ],
    "B": [
        (1, -3.9, "north"),
        (2, -3.9, "south"),
        (3, -1.3, "north"),
        (4, -1.3, "south"),
        (5, 1.3, "south"),
        (6, 1.3, "north"),
        (7, 3.9, "north"),
    ],
    "C": [
        (1, -2.6, "north"),
        (2, 0.0, "north"),
        (3, -2.6, "south"),
        (4, 2.6, "north"),
        (5, 0.0, "south"),
        (6, 2.6, "south"),
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
        for order, pos_x, bay_zone in LAYOUT[line_code]:
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
    # 설비별 고정 시드로 재현 가능한 잡음 생성 (hash는 프로세스마다 값이 달라 crc32 사용)
    is_normal = scenario_name == "normal"
    rng = random.Random(zlib.crc32(equipment_id.encode()))
    window: deque = deque(maxlen=8)
    prev_candidates: dict[str, str | None] = {}
    prev_composite: str | None = None  # 직전 tick 복합 후보 (ERR-402 2 tick 지속 확정용)
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
        # 정상 배치 설비는 노이즈 값만 유지하고 알람 억제, 통제된 데모·오탐 방지
        confirmed: dict[str, str | None] = {var: None for var in VARS}
        comp_confirmed: str | None = None
        if not is_normal:
            # 변수별 확정 (직전 tick과 동일 후보만)
            for var in VARS:
                cand = reading.alarm_codes.get(var)
                if cand is not None and cand == prev_candidates.get(var):
                    confirmed[var] = cand
            prev_candidates = dict(reading.alarm_codes)
            # 복합 냉각 고장(ERR-402)도 2 tick 지속 확정 (변수별과 동일 규칙, 대표 알람 오버레이)
            comp_cand = reading.composite_code
            if comp_cand is not None and comp_cand == prev_composite:
                comp_confirmed = comp_cand
            prev_composite = comp_cand
        # 복합이면 단일 코드 억제·대표 채널 ERR-402 부여 후 전이 산출 (매뉴얼 §5.1)
        surfaced = surface_codes(confirmed, comp_confirmed)
        # 변수별 발생/해제 전이 → equipment_alarms 이벤트
        for metric in VARS:
            code = surfaced[metric]
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
                alarm_code=representative(surfaced),
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


# 수리 이력 랜덤 생성 기간·건수 (BE_MCP05_MAINT01), 텔레메트리 앵커(7/8) 이전으로 설정
REPAIR_PERIOD_START = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
REPAIR_PERIOD_END = datetime(2026, 7, 7, 18, 0, tzinfo=UTC)
REPAIR_MIN_COUNT = 4  # 장비별 최소 건수
REPAIR_MAX_COUNT = 5  # 장비별 최대 건수

# 알람 코드별 수리 비고 풀, 코드 풀이 작아 동일 코드가 반복되며 반복 고장 패턴 재현
REPAIR_NOTES: dict[str, list[str]] = {
    "ERR-402": [
        "냉각 계통 고장, 냉각수 밸브 개방 조정·냉각팬 점검",
        "동일 냉각 고장 재발, 밸브 압력 재확보",
    ],
    "ERR-401": ["온도 급상승, 냉각 밸브 점검·교체", "동일 증상 재발, 냉각수 라인 세정"],
    "ERR-301": ["압력 이상, 배관 누설 보수", "진공 펌프 오일 교환·리크 체크"],
    "ERR-201": ["RF 파워 이상, 매칭 네트워크 조정", "RF 제너레이터 출력 캘리브레이션"],
    "WRN-501": ["가스 유량 저하, MFC 점검·퍼지", "가스 공급 라인 필터 교체"],
}
# 일반 설비 수리 비고 랜덤 풀 (복합 냉각 ERR-402는 A05 전용이라 제외)
_GENERAL_REPAIR_CODES = ["ERR-201", "ERR-301", "ERR-401", "WRN-501"]
# 특정 설비의 반복 고장 강제 (설비 → (알람 코드, 최소 반복 건수)), 재발 집계 데모 재현
_RECURRING_REPAIRS = {"EQP-A05": ("ERR-402", 2)}

# 팀 공용 로그인 계정 더미 배정 (BE_NOTI01_SCOPE01), field_engineer라 배정 라인 알림만 조회
# SSO 자동 프로비저닝이 이메일로 기존 유저를 연결하므로 선시드해도 로그인 시 정상 연결
SEED_USER_EMAIL = "agentory@minhoan11572025gmail.onmicrosoft.com"
SEED_USER_NAME = "Agentory 공용"
SEED_USER_ROLE = "field_engineer"
SEED_USER_LINE = "A라인"


def build_repairs() -> list[EquipmentRepair]:
    # 장비별 4~5건 수리 이력을 기간 내 랜덤 시각(분 단위)으로 부여 (BE_MCP05_MAINT01)
    # 문자열 시드 고정으로 재실행에도 동일 데이터 재현, repaired_by는 시드 범위 밖이라 NULL
    repairs: list[EquipmentRepair] = []
    period_minutes = int((REPAIR_PERIOD_END - REPAIR_PERIOD_START).total_seconds() // 60)
    for line_code, *_ in LINES:
        for order, *_rest in LAYOUT[line_code]:
            equipment_id = f"EQP-{line_code}{order:02d}"
            rng = random.Random(f"repair-{equipment_id}")
            count = rng.randint(REPAIR_MIN_COUNT, REPAIR_MAX_COUNT)
            offsets = sorted(rng.sample(range(period_minutes), count))
            picked = [rng.choice(_GENERAL_REPAIR_CODES) for _ in range(count)]
            recurring = _RECURRING_REPAIRS.get(equipment_id)
            if recurring is not None:
                # 지정 설비는 앞부분을 동일 코드로 강제해 반복 고장(재발) 이력 재현
                code, min_count = recurring
                for i in range(min(min_count, count)):
                    picked[i] = code
            elif len(set(picked)) == len(picked):
                picked[-1] = picked[0]  # 전부 다른 코드로 뽑히면 반복 고장 패턴 보장용 보정
            for offset, code in zip(offsets, picked, strict=True):
                repairs.append(
                    EquipmentRepair(
                        equipment_id=equipment_id,
                        repaired_by=None,
                        repaired_at=REPAIR_PERIOD_START + timedelta(minutes=offset),
                        alarm_code_before=code,
                        note=rng.choice(REPAIR_NOTES[code]),
                    )
                )
    return repairs


# UPSERT 갱신 대상 컬럼, PK(equipment_id)와 학습성 컬럼(alarm_cleared_at 등)은 제외해 기존 상태 보존
_MASTER_UPSERT_COLUMNS = (
    "line_name",
    "process_type",
    "location",
    "manager_dept",
    "manager_name",
    "last_inspection_at",
    "display_order",
    "shape",
    "bay_zone",
    "position_x",
    "position_y",
    "position_z",
    "rotation_y",
)


async def _upsert_masters(session, masters: list[EquipmentMaster]) -> None:
    """설비 마스터를 equipment_id 기준 UPSERT, 삭제 없이 정의 컬럼만 갱신 (#219)

    삭제-재적재 대신 on_conflict_do_update로 마스터를 유지해 자식 FK(work_logs·이상감지)를 보존
    시드에서 빠진 구 설비는 남지만 데모 구성상 설비 집합은 고정이라 별도 정리 불필요
    """
    values = [
        {
            "equipment_id": m.equipment_id,
            **{col: getattr(m, col) for col in _MASTER_UPSERT_COLUMNS},
        }
        for m in masters
    ]
    statement = pg_insert(EquipmentMaster).values(values)
    updated = {col: getattr(statement.excluded, col) for col in _MASTER_UPSERT_COLUMNS}
    statement = statement.on_conflict_do_update(
        index_elements=["equipment_id"],
        set_=updated,
    )
    await session.execute(statement)


async def seed() -> None:
    masters = build_masters()

    telemetry: list[EquipmentTelemetry] = []
    alarms: list[EquipmentAlarm] = []
    floor_demo = PRESETS["floor_demo"]  # 설비별 시나리오 배치 단일 소스 (라이브 시뮬레이터와 공유)
    for line_code, *_ in LINES:
        for order, _pos_x, _bay_zone in LAYOUT[line_code]:
            equipment_id = f"EQP-{line_code}{order:02d}"
            scenario_name = floor_demo.get(equipment_id, "normal")
            rows, alarm_rows = build_telemetry(equipment_id, scenario_name)
            telemetry.extend(rows)
            alarms.extend(alarm_rows)

    lines = build_lines()
    repairs = build_repairs()

    async with SessionLocal() as session:
        # 데모 데이터(telemetry·alarms·repairs) 비우고 재적재, FK 때문에 자식 먼저 삭제
        await session.execute(delete(EquipmentAlarm))
        await session.execute(delete(EquipmentTelemetry))
        await session.execute(delete(EquipmentRepair))
        # 설비 마스터는 삭제 대신 UPSERT, work_logs·이상감지 자식 FK 보존 (#219)
        await _upsert_masters(session, masters)
        # 라인 마스터 재적재, 배정(user_lines)은 라인 FK라 함께 비우고 아래에서 재시드
        await session.execute(delete(UserLine))
        await session.execute(delete(Line))
        session.add_all(lines)
        await session.flush()  # 라인·마스터 확정 후 자식(telemetry·alarms·repairs) FK 보장
        session.add_all(telemetry)
        session.add_all(alarms)
        session.add_all(repairs)
        # 공용 계정 더미 라인 배정, 유저는 로그인 이력 보존 위해 삭제하지 않고 이메일로 업서트
        user = (
            await session.execute(select(User).where(User.email == SEED_USER_EMAIL))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=SEED_USER_EMAIL, name=SEED_USER_NAME, role=SEED_USER_ROLE)
            session.add(user)
            await session.flush()
        line_a = next(line for line in lines if line.code == SEED_USER_LINE)
        session.add(UserLine(user_id=user.id, line_id=line_a.id))
        await session.commit()

    print(
        f"[seed] 라인 {len(lines)}건, 설비 {len(masters)}건, 텔레메트리 {len(telemetry)}건, "
        f"알람 이벤트 {len(alarms)}건, 수리 이력 {len(repairs)}건 적재 완료"
    )
    print(f"[seed] 공용 계정({SEED_USER_EMAIL}) {SEED_USER_LINE} 더미 배정 완료")
    print("[seed] 그 외 사용자 라인 배정은 비어 있음, 운영 화면에서 배정 필요")


if __name__ == "__main__":
    asyncio.run(seed())

"""센서 데이터 시뮬레이터 (BE_SIM01_GEN01)

실행: uv run simulator [--scenario ...] [--interval N] [--iterations N]
      [--target EQP-003] [--drift-start N] [--preset floor_demo] [--gain 5]
equipment_masters의 설비 목록을 대상으로 주기적으로 센서값을 생성해 equipment_telemetries에 적재
시나리오는 대상 설비에만 적용, 나머지 설비는 normal로 생성
preset 지정 시 설비별로 서로 다른 시나리오 배치, gain은 데모용 드리프트 증폭 배수
밴드 이탈은 첫 tick 즉시 알람으로 저장, 지연 없이 최초 이상 접촉을 발령 (참고서 §7)
골든 E2E 테스트는 --iterations 유한 모드로 시나리오를 결정론적으로 주입
"""

import argparse
import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentory.core.config import get_settings
from agentory.core.db import SessionLocal
from agentory.core.logging import setup_logging
from agentory.modules.telemetry.models import EquipmentAlarm, EquipmentMaster, EquipmentTelemetry
from simulator.generator import VARS, SensorReading, generate_reading
from simulator.scenarios import NORMAL, PRESETS, SCENARIOS

log = logging.getLogger("simulator")

PERSIST_RETRY = 1  # DB 실패 시 재시도 횟수
DRIFT_START_DEFAULT = 10  # 드리프트 시작 tick (참고서 §2)


@dataclass
class ScenarioConfig:
    """시뮬레이터 실행 설정, name은 SCENARIOS 키·골든 질의 셋 seed_scenario와 매핑"""

    name: str = "normal"
    interval_seconds: float = 5.0
    iterations: int | None = None  # None이면 무한 반복
    target_equipment_id: str = "EQP-003"  # 단일 시나리오 적용 대상 (preset 미사용 시)
    drift_start_tick: int = DRIFT_START_DEFAULT
    preset: str | None = None  # 다중 설비 시나리오 배치, 지정 시 target/scenario 대신 우선
    gain: float = 1.0  # PM 드리프트 증폭 배수, 데모 가시성 조절


async def _load_equipment(
    session_factory: async_sessionmaker,
) -> list[tuple[str, str, datetime | None]]:
    """equipment_masters에서 (equipment_id, process_type, repaired_at) 목록 로드

    repaired_at은 수리 힐 윈도우 판정용(NEW_REPAIR01_SIM01)
    DB 미준비·조회 실패 시 빈 목록 반환, 도커에서 시드 전에 떠도 죽지 않게 처리
    """
    try:
        async with session_factory() as session:
            rows = await session.scalars(select(EquipmentMaster))
            return [(e.equipment_id, e.process_type, e.repaired_at) for e in rows]
    except SQLAlchemyError as exc:
        log.warning("[simulator] 설비 목록 조회 실패: %s", exc)
        return []


async def _persist(
    session_factory: async_sessionmaker,
    readings: list[SensorReading],
    raises: list[tuple[str, str, str, str]],
    clears: list[tuple[str, str]],
    now: datetime,
) -> int:
    """센서값 배치 적재 + 변수별 알람 발생/해제 이벤트 기록, 실패 시 재시도 후 스킵

    telemetry 행과 equipment_alarms 전이를 같은 트랜잭션으로 적재, 반환은 telemetry 적재 건수
    """
    for attempt in range(PERSIST_RETRY + 1):
        try:
            async with session_factory() as session:
                session.add_all(
                    EquipmentTelemetry(
                        equipment_id=r.equipment_id,
                        temperature=r.temperature,
                        pressure=r.pressure,
                        rf_power=r.rf_power,
                        gas_flow=r.gas_flow,
                        alarm_code=r.alarm_code,
                    )
                    for r in readings
                )
                # 해제: 해당 변수의 열린 알람을 종료 처리
                for equipment_id, metric in clears:
                    await session.execute(
                        update(EquipmentAlarm)
                        .where(
                            EquipmentAlarm.equipment_id == equipment_id,
                            EquipmentAlarm.metric == metric,
                            EquipmentAlarm.cleared_at.is_(None),
                        )
                        .values(cleared_at=now)
                    )
                # 발생: 새 활성 알람 행 추가
                session.add_all(
                    EquipmentAlarm(
                        equipment_id=equipment_id,
                        metric=metric,
                        alarm_code=code,
                        severity=severity,
                        raised_at=now,
                    )
                    for equipment_id, metric, code, severity in raises
                )
                await session.commit()
            return len(readings)
        except SQLAlchemyError as exc:
            if attempt < PERSIST_RETRY:
                log.warning("[simulator] 적재 실패, 재시도 %d회차: %s", attempt + 1, exc)
            else:
                log.error("[simulator] 적재 재시도 실패, 이번 주기 스킵: %s", exc)
    return 0


def _severity(alarm_code: str) -> str:
    # 알람 코드 접두로 심각도 판정 (StatusLevel 값과 동일 표기), ERR=위험·그 외=주의
    return "위험" if alarm_code.startswith("ERR") else "주의"


def _alarm_transitions(
    readings: list[SensorReading], active_alarms: dict[str, dict[str, str]]
) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str]]]:
    # 변수별 확정 코드와 활성 알람을 비교해 발생(raise)·해제(clear) 이벤트 산출
    # 코드 변경은 기존 해제 + 신규 발생으로 처리, active_alarms 갱신은 커밋 성공 후 별도 반영
    raises: list[tuple[str, str, str, str]] = []
    clears: list[tuple[str, str]] = []
    for reading in readings:
        active = active_alarms.get(reading.equipment_id, {})
        for metric in VARS:
            code = reading.alarm_codes.get(metric)
            current = active.get(metric)
            if code == current:
                continue
            if current is not None:
                clears.append((reading.equipment_id, metric))
            if code is not None:
                raises.append((reading.equipment_id, metric, code, _severity(code)))
    return raises, clears


def _apply_active(
    active_alarms: dict[str, dict[str, str]],
    raises: list[tuple[str, str, str, str]],
    clears: list[tuple[str, str]],
) -> None:
    # 커밋 성공 후 인메모리 활성 알람 상태 반영, 해제 먼저 적용 후 발생 적용(코드 변경 대응)
    for equipment_id, metric in clears:
        active_alarms.get(equipment_id, {}).pop(metric, None)
    for equipment_id, metric, code, _severity_unused in raises:
        active_alarms.setdefault(equipment_id, {})[metric] = code


async def _load_active_alarms(
    session_factory: async_sessionmaker,
) -> dict[str, dict[str, str]]:
    # 시작 시 열린 알람으로 활성 상태 시드, 재시작해도 중복 발생·유령 해제 방지
    try:
        async with session_factory() as session:
            rows = await session.execute(
                select(
                    EquipmentAlarm.equipment_id, EquipmentAlarm.metric, EquipmentAlarm.alarm_code
                ).where(EquipmentAlarm.cleared_at.is_(None))
            )
            active: dict[str, dict[str, str]] = {}
            for equipment_id, metric, code in rows:
                active.setdefault(equipment_id, {})[metric] = code
            return active
    except SQLAlchemyError as exc:
        log.warning("[simulator] 활성 알람 시드 실패: %s", exc)
        return {}


def _select_spec(
    equipment_id: str,
    repaired_at: datetime | None,
    *,
    now: datetime,
    heal_window: timedelta,
    scenario_map: dict[str, str] | None,
    scenario,
    target_equipment_id: str,
):
    # 설비별 시나리오 결정 (NEW_REPAIR01_SIM01)
    # 수리 힐 윈도우 내면 시나리오 무관 정상 강제, 경과 후 원래 시나리오 재개(재고장)
    if repaired_at is not None and now - repaired_at < heal_window:
        return NORMAL
    # preset이면 설비별 배치, 아니면 단일 target에만 시나리오 적용 후 나머지 정상
    if scenario_map is not None:
        return SCENARIOS[scenario_map.get(equipment_id, "normal")]
    return scenario if equipment_id == target_equipment_id else NORMAL


async def run_simulation(
    config: ScenarioConfig, session_factory: async_sessionmaker = SessionLocal
) -> None:
    rng = random.Random()
    scenario = SCENARIOS[config.name]
    # preset 지정 시 설비별 시나리오 맵, 미지정 설비는 normal
    scenario_map = PRESETS[config.preset] if config.preset else None
    # 수리 힐 윈도우, 수리 후 이 기간 동안 정상 강제 후 원래 시나리오 재개 (NEW_REPAIR01_SIM01)
    heal_window = timedelta(minutes=get_settings().sim_repair_heal_minutes)
    history: dict[str, deque] = {}  # 설비별 최근 센서값 (WRN-801 이동창 판정용)
    active_alarms = await _load_active_alarms(session_factory)  # 설비별·변수별 활성 알람 상태
    tick = 0
    while config.iterations is None or tick < config.iterations:
        equipment = await _load_equipment(session_factory)
        if not equipment:
            log.warning("[simulator] equipment_masters 비어 있음(또는 DB 미준비), 대기")
            if config.iterations is not None:
                return  # 유한 모드는 즉시 종료
            await asyncio.sleep(config.interval_seconds)
            continue

        now = datetime.now(UTC)
        readings = []
        for equipment_id, process_type, repaired_at in equipment:
            spec = _select_spec(
                equipment_id,
                repaired_at,
                now=now,
                heal_window=heal_window,
                scenario_map=scenario_map,
                scenario=scenario,
                target_equipment_id=config.target_equipment_id,
            )
            window = history.setdefault(equipment_id, deque(maxlen=8))
            reading = generate_reading(
                equipment_id,
                process_type,
                scenario=spec,
                tick=tick,
                drift_start_tick=config.drift_start_tick,
                history=list(window),
                gain=config.gain,
                rng=rng,
            )
            window.append(reading)
            readings.append(reading)

        # 변수별 발생/해제 전이 산출 후 telemetry와 함께 적재, 성공 시 활성 상태 반영
        raises, clears = _alarm_transitions(readings, active_alarms)
        inserted = await _persist(session_factory, readings, raises, clears, now)
        if inserted:
            _apply_active(active_alarms, raises, clears)
        mode = f"preset={config.preset}" if config.preset else f"scenario={config.name}"
        log.info("[simulator] tick %d, %d건 적재 (%s)", tick, inserted, mode)

        tick += 1
        if config.iterations is None or tick < config.iterations:
            await asyncio.sleep(config.interval_seconds)


def _parse_args() -> ScenarioConfig:
    parser = argparse.ArgumentParser(description="센서 데이터 시뮬레이터 (BE_SIM01_GEN01)")
    parser.add_argument("--scenario", default="normal", choices=list(SCENARIOS))
    parser.add_argument("--interval", type=float, default=5.0, help="생성 주기 초")
    parser.add_argument("--iterations", type=int, default=None, help="반복 횟수, 미지정 시 무한")
    parser.add_argument("--target", default="EQP-003", help="시나리오 적용 대상 설비")
    parser.add_argument(
        "--drift-start", type=int, default=DRIFT_START_DEFAULT, help="드리프트 시작 tick"
    )
    parser.add_argument(
        "--preset", default=None, choices=list(PRESETS), help="다중 설비 시나리오 배치"
    )
    parser.add_argument("--gain", type=float, default=1.0, help="PM 드리프트 증폭 배수")
    args = parser.parse_args()
    return ScenarioConfig(
        name=args.scenario,
        interval_seconds=args.interval,
        iterations=args.iterations,
        target_equipment_id=args.target,
        drift_start_tick=args.drift_start,
        preset=args.preset,
        gain=args.gain,
    )


def run() -> None:
    setup_logging()
    asyncio.run(run_simulation(_parse_args()))

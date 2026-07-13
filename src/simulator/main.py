"""센서 데이터 시뮬레이터 (BE_SIM01_GEN01)

실행: uv run simulator [--scenario ...] [--interval N] [--iterations N]
      [--target EQP-003] [--drift-start N] [--preset floor_demo] [--gain 5]
equipment_masters의 설비 목록을 대상으로 주기적으로 센서값을 생성해 equipment_telemetries에 적재
시나리오는 대상 설비에만 적용, 나머지 설비는 normal로 생성
preset 지정 시 설비별로 서로 다른 시나리오 배치, gain은 데모용 드리프트 증폭 배수
1 tick 스파이크는 대표 알람 보류, 연속 2 tick 이상 지속한 후보만 알람으로 저장 (참고서 §7)
골든 E2E 테스트는 --iterations 유한 모드로 시나리오를 결정론적으로 주입
"""

import argparse
import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentory.core.config import get_settings
from agentory.core.db import SessionLocal
from agentory.core.logging import setup_logging
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from simulator.generator import SensorReading, generate_reading
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


async def _persist(session_factory: async_sessionmaker, readings: list[SensorReading]) -> int:
    """센서값 배치 적재, 실패 시 재시도 후 스킵, 반환은 적재 건수"""
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
                await session.commit()
            return len(readings)
        except SQLAlchemyError as exc:
            if attempt < PERSIST_RETRY:
                log.warning("[simulator] 적재 실패, 재시도 %d회차: %s", attempt + 1, exc)
            else:
                log.error("[simulator] 적재 재시도 실패, 이번 주기 스킵: %s", exc)
    return 0


def _confirm_persistence(reading: SensorReading, prev_candidate: dict[str, str | None]) -> None:
    # 연속 2 tick 지속 규칙: 직전 tick과 같은 후보만 대표 알람으로 확정, 1 tick 스파이크는 보류
    candidate = reading.alarm_code
    reading.alarm_code = (
        candidate
        if candidate is not None and candidate == prev_candidate.get(reading.equipment_id)
        else None
    )
    prev_candidate[reading.equipment_id] = candidate


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
    prev_candidate: dict[str, str | None] = {}  # 설비별 직전 tick 후보 알람
    history: dict[str, deque] = {}  # 설비별 최근 센서값 (WRN-801 이동창 판정용)
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
            _confirm_persistence(reading, prev_candidate)
            readings.append(reading)

        inserted = await _persist(session_factory, readings)
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

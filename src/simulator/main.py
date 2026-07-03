"""센서 데이터 시뮬레이터 (BE_SIM01_GEN01)

실행: uv run simulator [--scenario ...] [--interval N] [--iterations N] [--target EQP-003]
equipment_master의 설비 목록을 대상으로 주기적으로 센서값을 생성해 equipment_telemetry에 적재
시나리오 모드(err402_temp_rise) 활성 시 대상 설비에 온도 상승·ERR-402를 주입
골든 E2E 테스트(3주차)는 --iterations 유한 모드로 시나리오를 결정론적으로 주입
"""

import argparse
import asyncio
import logging
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentory.core.db import SessionLocal
from agentory.core.logging import setup_logging
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from simulator.generator import SensorReading, generate_reading

log = logging.getLogger("simulator")

ANOMALY_SCENARIO = "err402_temp_rise"
PERSIST_RETRY = 1  # DB 실패 시 재시도 횟수


@dataclass
class ScenarioConfig:
    """시뮬레이터 실행 설정, name은 골든 질의 셋 seed_scenario 키와 매핑"""

    name: str = "normal"  # normal | err402_temp_rise
    interval_seconds: float = 5.0
    iterations: int | None = None  # None이면 무한 반복
    target_equipment_id: str = "EQP-003"  # 이상 시나리오 대상


async def _load_equipment(session_factory: async_sessionmaker) -> list[tuple[str, str]]:
    """equipment_master에서 (equipment_id, process_type) 목록 로드

    DB 미준비·조회 실패 시 빈 목록 반환, 도커에서 시드 전에 떠도 죽지 않게 처리
    """
    try:
        async with session_factory() as session:
            rows = await session.scalars(select(EquipmentMaster))
            return [(e.equipment_id, e.process_type) for e in rows]
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


async def run_simulation(
    config: ScenarioConfig, session_factory: async_sessionmaker = SessionLocal
) -> None:
    rng = random.Random()
    steps: dict[str, int] = {}  # 이상 시나리오 대상 설비별 진행 step
    tick = 0
    while config.iterations is None or tick < config.iterations:
        equipment = await _load_equipment(session_factory)
        if not equipment:
            log.warning("[simulator] equipment_master 비어 있음(또는 DB 미준비), 대기")
            if config.iterations is not None:
                return  # 유한 모드는 즉시 종료
            await asyncio.sleep(config.interval_seconds)
            continue

        readings = []
        for equipment_id, process_type in equipment:
            anomaly = config.name == ANOMALY_SCENARIO and equipment_id == config.target_equipment_id
            reading = generate_reading(
                equipment_id,
                process_type,
                anomaly=anomaly,
                step=steps.get(equipment_id, 0),
                rng=rng,
            )
            readings.append(reading)
            if anomaly:
                steps[equipment_id] = steps.get(equipment_id, 0) + 1

        inserted = await _persist(session_factory, readings)
        log.info("[simulator] tick %d, %d건 적재 (scenario=%s)", tick, inserted, config.name)

        tick += 1
        if config.iterations is None or tick < config.iterations:
            await asyncio.sleep(config.interval_seconds)


def _parse_args() -> ScenarioConfig:
    parser = argparse.ArgumentParser(description="센서 데이터 시뮬레이터 (BE_SIM01_GEN01)")
    parser.add_argument("--scenario", default="normal", choices=["normal", ANOMALY_SCENARIO])
    parser.add_argument("--interval", type=float, default=5.0, help="생성 주기 초")
    parser.add_argument("--iterations", type=int, default=None, help="반복 횟수, 미지정 시 무한")
    parser.add_argument("--target", default="EQP-003", help="이상 시나리오 대상 설비")
    args = parser.parse_args()
    return ScenarioConfig(
        name=args.scenario,
        interval_seconds=args.interval,
        iterations=args.iterations,
        target_equipment_id=args.target,
    )


def run() -> None:
    setup_logging()
    asyncio.run(run_simulation(_parse_args()))

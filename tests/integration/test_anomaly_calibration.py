"""설비별 캘리브레이션 통합 테스트 (BE_ANOM01_CALIB01)

실제 DB 필요, 미연결 시 스킵, flush만 하고 teardown rollback으로 미오염
격리된 공정 유형·설비로 캘리브 산출·적재·폴백 검증
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from agentory.modules.watcher import anomaly_repository as repo
from agentory.modules.watcher.fit import fit_in_session
from agentory.modules.watcher.models import EquipmentAnomalyCalibration

PTYPE = "ZZZTEST-CALIB"
EQP_A = "ZZZ-CALIB-A"  # 표본 충분, 캘리브 산출 대상
EQP_B = "ZZZ-CALIB-B"  # 표본 부족, 공정 유형 한계 폴백
NOW = datetime(2026, 7, 16, tzinfo=UTC)


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


def _coupled(rng, n, offset=0.0):
    base = rng.normal(0, 1, size=(n, 1)) + offset
    coupled = base + rng.normal(0, 0.1, size=(n, 1))
    return np.concatenate([base, coupled, rng.normal(0, 1, size=(n, 2))], axis=1)


def _row(eqp, row, ts):
    return EquipmentTelemetry(
        equipment_id=eqp,
        timestamp=ts,
        temperature=float(row[0]),
        pressure=float(row[1]),
        rf_power=float(row[2]),
        gas_flow=float(row[3]),
    )


async def _seed(session):
    rng = np.random.default_rng(0)
    for eqp in (EQP_A, EQP_B):
        session.add(EquipmentMaster(equipment_id=eqp, process_type=PTYPE, line_name="ZZ"))
    await session.flush()
    # EQP_A: 표본 충분(1500행 → 약 248 윈도우), EQP_B: 부족(300행 → 약 49 윈도우)
    for i, row in enumerate(_coupled(rng, 1500)):
        session.add(_row(EQP_A, row, NOW + timedelta(seconds=5 * i)))
    for i, row in enumerate(_coupled(rng, 300)):
        session.add(_row(EQP_B, row, NOW + timedelta(seconds=5 * i)))
    await session.flush()


async def test_calibration_saved_for_sufficient_equipment_only(session):
    await _seed(session)
    fitted = await fit_in_session(session, get_settings())
    await session.flush()
    assert fitted.get(PTYPE) is not None

    rows = await session.execute(
        select(EquipmentAnomalyCalibration.equipment_id).where(
            EquipmentAnomalyCalibration.equipment_id.in_([EQP_A, EQP_B])
        )
    )
    calibrated = {eid for (eid,) in rows}
    # 표본 충분 설비만 캘리브 저장, 부족 설비는 미저장 (공정 한계 폴백)
    assert EQP_A in calibrated
    assert EQP_B not in calibrated


async def test_scorer_loads_equipment_limits_with_fallback(session):
    await _seed(session)
    await fit_in_session(session, get_settings())
    await session.flush()

    limits = await repo.load_equipment_limits(session)
    assert EQP_A in limits and EQP_B not in limits

    scorer = await repo.load_scorer(session, get_settings())
    series = await repo.fetch_recent_series(session, EQP_A, 300)
    # 캘리브 보유·미보유 설비 모두 판정 가능 (미보유는 공정 한계 폴백)
    assert scorer.score_latest(PTYPE, EQP_A, series) is not None
    assert scorer.score_latest(PTYPE, EQP_B, series) is not None

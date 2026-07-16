"""이상 감지 재적합 통합 테스트 (BE_ANOM01_DRIFT01)

실제 DB 필요, 미연결 시 스킵, flush만 하고 teardown rollback으로 미오염
격리된 공정 유형·설비로 재적합의 최근성·정비 인지·staleness 스킵 검증
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from agentory.modules.watcher.fit import fit_in_session
from agentory.modules.watcher.models import EquipmentAnomalyModel

PTYPE = "ZZZTEST-DRIFT"
EQP = "ZZZ-DRIFT-01"
NOW = datetime(2026, 7, 16, tzinfo=UTC)
REPAIR = NOW - timedelta(days=3)


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


def _coupled(rng, n, scale=1.0):
    base = rng.normal(0, scale, size=(n, 1))
    coupled = base + rng.normal(0, 0.1, size=(n, 1))
    return np.concatenate([base, coupled, rng.normal(0, scale, size=(n, 2))], axis=1)


async def _seed(session, repaired_at):
    session.add(
        EquipmentMaster(
            equipment_id=EQP, process_type=PTYPE, line_name="ZZ", repaired_at=repaired_at
        )
    )
    await session.flush()
    rng = np.random.default_rng(0)
    # 정비 전(먼 과거) 왜곡된 정상 + 정비 후(최근) 정상, 둘 다 alarm_code NULL
    pre = _coupled(rng, 3000, scale=5.0)  # 정비 전 열화(분산 큼), 포함 시 학습 윈도우 급증
    post = _coupled(rng, 1500, scale=1.0)  # 정비 후 정상, 단독 약 248 윈도우
    for i, row in enumerate(pre):
        session.add(_row(row, NOW - timedelta(days=10) + timedelta(seconds=5 * i)))
    for i, row in enumerate(post):
        session.add(_row(row, REPAIR + timedelta(seconds=5 * i)))
    await session.flush()


def _row(row, ts):
    return EquipmentTelemetry(
        equipment_id=EQP,
        timestamp=ts,
        temperature=float(row[0]),
        pressure=float(row[1]),
        rf_power=float(row[2]),
        gas_flow=float(row[3]),
    )


async def test_refit_excludes_pre_repair_data(session):
    await _seed(session, repaired_at=REPAIR)
    fitted = await fit_in_session(session, get_settings(), recency_days=30)
    await session.flush()
    assert fitted.get(PTYPE) is not None
    row = await session.scalar(
        select(EquipmentAnomalyModel).where(EquipmentAnomalyModel.process_type == PTYPE)
    )
    # 정비 후 데이터(1500행)만 학습, 정비 전(3000행) 제외 → 정비 후 윈도우 수에 근접
    assert row.train_rows < 300  # 정비 후 약 248 윈도우, 정비 전 포함 시 약 745


async def test_refit_only_stale_skips_fresh_model(session):
    await _seed(session, repaired_at=REPAIR)
    settings = get_settings()
    # 신선한 모델 선삽입 (방금 적합)
    await fit_in_session(session, settings, recency_days=30)
    await session.flush()
    # only_stale 재적합은 신선 모델 스킵
    refitted = await fit_in_session(session, settings, recency_days=30, only_stale=True)
    assert PTYPE not in refitted

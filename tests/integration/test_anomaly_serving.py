"""이상 감지 서빙 통합 테스트 (BE_ANOM01_SERVE01)

실제 DB 필요, 미연결 시 스킵, flush만 하고 teardown rollback으로 미오염
격리된 공정 유형·설비·2000년 창으로 전역 데이터와 분리
검증: 스코어러 발령 → EquipmentAlarm 기록 → sync_from_alarms → notification 생성 (파이프라인 재사용)
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.notification import repository as notif_repo
from agentory.modules.notification.models import Notification
from agentory.modules.telemetry.models import EquipmentAlarm, EquipmentMaster, EquipmentTelemetry
from agentory.modules.watcher import anomaly_repository as repo
from agentory.modules.watcher.detector import ANOMALY_ALARM_CODE, VARS
from anomaly.models.pca_mspc import PcaMspc
from anomaly.scoring import ewma
from anomaly.windowing import sliding_windows

PTYPE = "ZZZTEST-ANOM"
EQP = "ZZZ-ANOM-01"
Y2000 = datetime(2000, 1, 1, 0, 0, tzinfo=UTC)
WINDOW, STRIDE, K = 12, 6, 2


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


def _coupled(rng, n):
    base = rng.normal(0, 1, size=(n, 1))
    coupled = base + rng.normal(0, 0.1, size=(n, 1))
    return np.concatenate([base, coupled, rng.normal(0, 1, size=(n, 2))], axis=1)


async def _seed_model_and_master(session):
    rng = np.random.default_rng(0)
    train = sliding_windows(_coupled(rng, 4000), WINDOW, STRIDE)
    model = PcaMspc(0.9, 0.999, cross_correlation=True).fit(train)
    limit = max(float(np.quantile(ewma(np.minimum(model.score(train), 3.0), 0.1), 0.999)), 1e-12)
    session.add(EquipmentMaster(equipment_id=EQP, process_type=PTYPE, line_name="ZZ"))
    await session.flush()
    await repo.save_model(
        session,
        process_type=PTYPE,
        window=WINDOW,
        stride=STRIDE,
        confirm_k=K,
        ewma_limit=limit,
        state=model.to_state(),
        train_rows=int(train.shape[0]),
    )
    await session.flush()


async def _insert_series(session, matrix):
    for i, row in enumerate(matrix):
        session.add(
            EquipmentTelemetry(
                equipment_id=EQP,
                timestamp=Y2000 + timedelta(seconds=5 * i),
                temperature=float(row[0]),
                pressure=float(row[1]),
                rf_power=float(row[2]),
                gas_flow=float(row[3]),
            )
        )
    await session.flush()


async def test_scorer_fires_on_anomalous_tail_and_flows_to_notification(session):
    await _seed_model_and_master(session)
    rng = np.random.default_rng(7)
    series = _coupled(rng, 300)
    series[150:, 0] += 6.0  # temperature 채널 지속 이탈
    await _insert_series(session, series)

    scorer = await repo.load_scorer(session, get_settings())
    assert scorer.has(PTYPE)
    fetched = await repo.fetch_recent_series(session, EQP, 300)
    result = scorer.score_latest(PTYPE, fetched)
    assert result is not None and result.fired is True
    assert result.channel == VARS[0]

    # 발령 → EquipmentAlarm 기록 → sync_from_alarms → notification 생성
    await repo.raise_anomaly(session, EQP, result.channel, Y2000 + timedelta(seconds=2000))
    await session.flush()
    active = await repo.active_anomaly_equipment(session)
    assert EQP in active

    await notif_repo.sync_from_alarms(session)
    notif = await session.scalar(
        select(Notification).where(
            Notification.equipment_id == EQP, Notification.alarm_code == ANOMALY_ALARM_CODE
        )
    )
    assert notif is not None
    assert notif.metric == VARS[0]


async def test_normal_series_does_not_fire(session):
    await _seed_model_and_master(session)
    await _insert_series(session, _coupled(np.random.default_rng(3), 300))
    scorer = await repo.load_scorer(session, get_settings())
    result = scorer.score_latest(PTYPE, await repo.fetch_recent_series(session, EQP, 300))
    assert result is not None and result.fired is False
    # 미발령이므로 활성 알람 없음
    alarms = await session.scalar(
        select(EquipmentAlarm).where(
            EquipmentAlarm.equipment_id == EQP, EquipmentAlarm.alarm_code == ANOMALY_ALARM_CODE
        )
    )
    assert alarms is None

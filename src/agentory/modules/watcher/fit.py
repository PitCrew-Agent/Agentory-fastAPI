"""이상 감지 모델 학습·재적합 CLI (BE_ANOM01_SERVE01, BE_ANOM01_DRIFT01)

실행: uv run anomaly-fit
공정 유형별 정상 telemetry(alarm_code IS NULL)로 PCAX 적합 후 EquipmentAnomalyModel 저장
서빙 파라미터(윈도우·stride·K·EWMA)는 config 확정값 사용 (EXP-006·007)
재적합(fit_all recency_days·only_stale)은 완만한 정상 이동 추종, 설비별 정비 이전 제외
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.config import Settings, get_settings
from agentory.core.db import SessionLocal
from agentory.core.logging import setup_logging
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from agentory.modules.watcher import anomaly_repository as repo
from agentory.modules.watcher.detector import VARS
from agentory.modules.watcher.models import EquipmentAnomalyModel
from anomaly.models.pca_mspc import PcaMspc
from anomaly.scoring import ewma
from anomaly.windowing import sliding_windows

log = logging.getLogger("anomaly-fit")

# 공정 유형당 최소 학습 윈도우 수, 미달 시 스킵 (표본 부족 모델 방지)
MIN_TRAIN_WINDOWS = 200
# 설비별 정상 조회 상한, 최근 구간 우선
NORMAL_ROWS_PER_EQUIPMENT = 20000


def _effective_since(
    recency_cutoff: datetime | None, repaired_at: datetime | None
) -> datetime | None:
    # 최근성 경계와 정비 시점 중 늦은 쪽, 정비 이전 열화 정상을 학습에서 제외
    bounds = [b for b in (recency_cutoff, repaired_at) if b is not None]
    return max(bounds) if bounds else None


async def _equipment_by_type(
    session: AsyncSession,
) -> dict[str, list[tuple[str, datetime | None]]]:
    rows = await session.execute(
        select(
            EquipmentMaster.process_type,
            EquipmentMaster.equipment_id,
            EquipmentMaster.repaired_at,
        )
    )
    by_type: dict[str, list[tuple[str, datetime | None]]] = {}
    for ptype, eid, repaired_at in rows:
        by_type.setdefault(ptype, []).append((eid, repaired_at))
    return by_type


async def _fitted_at_by_type(session: AsyncSession) -> dict[str, datetime]:
    rows = await session.execute(
        select(EquipmentAnomalyModel.process_type, EquipmentAnomalyModel.fitted_at)
    )
    return {ptype: fitted_at for ptype, fitted_at in rows}


async def _normal_windows(
    session: AsyncSession,
    equipment_id: str,
    window: int,
    stride: int,
    since: datetime | None,
) -> np.ndarray:
    # 정상(alarm_code IS NULL) 센서값을 시간 오름차순 윈도우로 변환, since 이후만
    columns = [getattr(EquipmentTelemetry, var) for var in VARS]
    conditions = [
        EquipmentTelemetry.equipment_id == equipment_id,
        EquipmentTelemetry.alarm_code.is_(None),
    ]
    if since is not None:
        conditions.append(EquipmentTelemetry.timestamp >= since)
    rows = await session.execute(
        select(*columns)
        .where(*conditions)
        .order_by(EquipmentTelemetry.timestamp.desc())
        .limit(NORMAL_ROWS_PER_EQUIPMENT)
    )
    values = [[float(v) for v in row] for row in rows if all(v is not None for v in row)]
    values.reverse()
    if len(values) < window:
        return np.empty((0, window, len(VARS)))
    return sliding_windows(np.array(values, dtype=float), window, stride)


async def fit_in_session(
    session: AsyncSession,
    settings: Settings,
    *,
    recency_days: int | None = None,
    only_stale: bool = False,
) -> dict[str, int]:
    """세션 내 공정 유형별 적합·저장 (커밋은 호출측), 반환은 유형별 학습 윈도우 수

    recency_days 지정 시 최근 구간만, only_stale 지정 시 신선한 모델은 스킵 (재적합용)
    """
    window = settings.anomaly_window_ticks
    stride = settings.anomaly_stride_ticks
    now = datetime.now(UTC)
    recency_cutoff = now - timedelta(days=recency_days) if recency_days else None
    stale_cutoff = now - timedelta(hours=settings.anomaly_refit_stale_hours)
    fitted: dict[str, int] = {}
    by_type = await _equipment_by_type(session)
    fitted_at = await _fitted_at_by_type(session) if only_stale else {}
    for ptype, equipment in by_type.items():
        if only_stale and (last := fitted_at.get(ptype)) is not None and last >= stale_cutoff:
            continue  # 아직 신선, 재적합 불요
        parts = []
        for eid, repaired_at in equipment:
            since = _effective_since(recency_cutoff, repaired_at)
            w = await _normal_windows(session, eid, window, stride, since)
            if w.shape[0] > 0:
                parts.append(w)
        train = np.concatenate(parts) if parts else np.empty((0, window, len(VARS)))
        if train.shape[0] < MIN_TRAIN_WINDOWS:
            # 표본 부족 시 기존 모델 유지 (좋은 모델을 나쁜 데이터로 덮어쓰지 않음)
            log.warning("[anomaly-fit] %s 학습 윈도우 부족(%d), 스킵", ptype, train.shape[0])
            continue
        model = PcaMspc(0.9, 0.999, cross_correlation=True).fit(train)
        accumulated = ewma(
            np.minimum(model.score(train), settings.anomaly_ewma_clip),
            settings.anomaly_ewma_alpha,
        )
        ewma_limit = max(float(np.quantile(accumulated, 0.999)), 1e-12)
        await repo.save_model(
            session,
            process_type=ptype,
            window=window,
            stride=stride,
            confirm_k=settings.anomaly_confirm_k,
            ewma_limit=ewma_limit,
            state=model.to_state(),
            train_rows=int(train.shape[0]),
        )
        fitted[ptype] = int(train.shape[0])
        log.info("[anomaly-fit] %s 적합 완료, 학습 윈도우 %d", ptype, train.shape[0])
    return fitted


async def fit_all(
    settings: Settings, *, recency_days: int | None = None, only_stale: bool = False
) -> dict[str, int]:
    """공정 유형별 모델 적합·저장 (SessionLocal + 커밋), CLI·재적합 워처 진입점"""
    async with SessionLocal() as session:
        fitted = await fit_in_session(
            session, settings, recency_days=recency_days, only_stale=only_stale
        )
        await session.commit()
    return fitted


def run() -> None:
    setup_logging()
    fitted = asyncio.run(fit_all(get_settings()))
    if not fitted:
        log.warning("[anomaly-fit] 적합된 모델 없음, 정상 데이터 확보 후 재실행 필요")
    for ptype, rows in fitted.items():
        log.info("[anomaly-fit] %s: %d 윈도우", ptype, rows)

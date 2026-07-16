"""이상 감지 모델 학습 CLI (BE_ANOM01_SERVE01)

실행: uv run anomaly-fit
공정 유형별 정상 telemetry(alarm_code IS NULL)로 PCAX 적합 후 EquipmentAnomalyModel 저장
서빙 파라미터(윈도우·stride·K·EWMA)는 config 확정값 사용 (EXP-006·007)
"""

import asyncio
import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.config import Settings, get_settings
from agentory.core.db import SessionLocal
from agentory.core.logging import setup_logging
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from agentory.modules.watcher import anomaly_repository as repo
from agentory.modules.watcher.detector import VARS
from anomaly.models.pca_mspc import PcaMspc
from anomaly.scoring import ewma
from anomaly.windowing import sliding_windows

log = logging.getLogger("anomaly-fit")

# 공정 유형당 최소 학습 윈도우 수, 미달 시 스킵 (표본 부족 모델 방지)
MIN_TRAIN_WINDOWS = 200
# 설비별 정상 조회 상한, 최근 구간 우선
NORMAL_ROWS_PER_EQUIPMENT = 20000


async def _equipment_by_type(session: AsyncSession) -> dict[str, list[str]]:
    rows = await session.execute(select(EquipmentMaster.process_type, EquipmentMaster.equipment_id))
    by_type: dict[str, list[str]] = {}
    for ptype, eid in rows:
        by_type.setdefault(ptype, []).append(eid)
    return by_type


async def _normal_windows(
    session: AsyncSession, equipment_id: str, window: int, stride: int
) -> np.ndarray:
    # 정상(alarm_code IS NULL) 센서값을 시간 오름차순 윈도우로 변환
    columns = [getattr(EquipmentTelemetry, var) for var in VARS]
    rows = await session.execute(
        select(*columns)
        .where(
            EquipmentTelemetry.equipment_id == equipment_id,
            EquipmentTelemetry.alarm_code.is_(None),
        )
        .order_by(EquipmentTelemetry.timestamp.desc())
        .limit(NORMAL_ROWS_PER_EQUIPMENT)
    )
    values = [[float(v) for v in row] for row in rows if all(v is not None for v in row)]
    values.reverse()
    if len(values) < window:
        return np.empty((0, window, len(VARS)))
    return sliding_windows(np.array(values, dtype=float), window, stride)


async def fit_all(settings: Settings) -> dict[str, int]:
    """공정 유형별 모델 적합·저장, 반환은 유형별 학습 윈도우 수"""
    window = settings.anomaly_window_ticks
    stride = settings.anomaly_stride_ticks
    fitted: dict[str, int] = {}
    async with SessionLocal() as session:
        by_type = await _equipment_by_type(session)
        for ptype, equipment_ids in by_type.items():
            parts = [
                w
                for eid in equipment_ids
                if (w := await _normal_windows(session, eid, window, stride)).shape[0] > 0
            ]
            train = np.concatenate(parts) if parts else np.empty((0, window, len(VARS)))
            if train.shape[0] < MIN_TRAIN_WINDOWS:
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
        await session.commit()
    return fitted


def run() -> None:
    setup_logging()
    fitted = asyncio.run(fit_all(get_settings()))
    if not fitted:
        log.warning("[anomaly-fit] 적합된 모델 없음, 정상 데이터 확보 후 재실행 필요")
    for ptype, rows in fitted.items():
        log.info("[anomaly-fit] %s: %d 윈도우", ptype, rows)

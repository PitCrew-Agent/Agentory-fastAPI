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
# 설비별 캘리브 최소 윈도우 수, 미달 설비는 공정 유형 한계로 폴백 (BE_ANOM01_CALIB01)
MIN_CALIBRATION_WINDOWS = 200
# 설비별 정상 조회 상한, 최근 구간 우선
NORMAL_ROWS_PER_EQUIPMENT = 20000
# EWMA 워밍업 제외 길이(시정수 배수), 초기 전이가 극단 분위수를 부풀리는 것 차단
EWMA_WARMUP_TAUS = 5
# 워밍업 제외 후 최소 잔여 윈도우, 미달 시 전체 사용
MIN_STEADY_WINDOWS = 100


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
        per_equipment: list[tuple[str, np.ndarray]] = []
        for eid, repaired_at in equipment:
            since = _effective_since(recency_cutoff, repaired_at)
            w = await _normal_windows(session, eid, window, stride, since)
            if w.shape[0] > 0:
                per_equipment.append((eid, w))
        train = (
            np.concatenate([w for _, w in per_equipment])
            if per_equipment
            else np.empty((0, window, len(VARS)))
        )
        if train.shape[0] < MIN_TRAIN_WINDOWS:
            # 표본 부족 시 기존 모델 유지 (좋은 모델을 나쁜 데이터로 덮어쓰지 않음)
            log.warning("[anomaly-fit] %s 학습 윈도우 부족(%d), 스킵", ptype, train.shape[0])
            continue
        model = PcaMspc(0.9, 0.999, cross_correlation=True).fit(train)
        ewma_limit = _limit_from_parts([w for _, w in per_equipment], model, settings)
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
        # 설비별 캘리브 한계, 개체 정상 분포로 산출 (BE_ANOM01_CALIB01)
        for eid, w in per_equipment:
            if w.shape[0] < MIN_CALIBRATION_WINDOWS:
                continue  # 표본 부족 설비는 공정 유형 한계로 폴백
            await repo.save_calibration(
                session,
                equipment_id=eid,
                ewma_limit=_clamped_equipment_limit(
                    _limit_from_windows(w, model, settings), ewma_limit, settings
                ),
                train_windows=int(w.shape[0]),
            )
        fitted[ptype] = int(train.shape[0])
        log.info("[anomaly-fit] %s 적합 완료, 학습 윈도우 %d", ptype, train.shape[0])
    return fitted


def _limit_from_windows(windows: np.ndarray, model: PcaMspc, settings: Settings) -> float:
    """정상 윈도우 EWMA 점수 분위수로 한계 산출 (공정·설비 공통)

    EWMA 워밍업 구간 제외가 필수, 초기값(scores[0])이 높으면 감쇠 구간이 극단 분위수를
    독점해 한계가 수배 부풀려짐. 스코어러는 정상 상태 윈도우로 판정하므로 한계도 동일
    구간에서 캘리브해야 일관 (BE_ANOM01_CALIB01 진단)
    """
    return max(float(np.quantile(_steady_scores(windows, model, settings), 0.999)), 1e-12)


def _steady_scores(windows: np.ndarray, model: PcaMspc, settings: Settings) -> np.ndarray:
    # 단일 연속 시퀀스의 EWMA 정상 상태 구간, 워밍업 제외
    accumulated = ewma(
        np.minimum(model.score(windows), settings.anomaly_ewma_clip),
        settings.anomaly_ewma_alpha,
    )
    warmup = int(EWMA_WARMUP_TAUS / settings.anomaly_ewma_alpha)
    steady = accumulated[warmup:]
    return accumulated if steady.size < MIN_STEADY_WINDOWS else steady


def _limit_from_parts(parts: list[np.ndarray], model: PcaMspc, settings: Settings) -> float:
    """설비별 시퀀스를 각각 EWMA 후 결합해 공정 유형 한계 산출

    이어붙인 시퀀스에 EWMA를 통째로 돌리면 설비 경계마다 전이가 생겨 극단 분위수가
    부풀려짐, 실험 하네스(run.py)와 동일하게 파트별 계산 후 결합 (BE_ANOM01_CALIB01 진단)
    """
    pooled = np.concatenate([_steady_scores(w, model, settings) for w in parts])
    return max(float(np.quantile(pooled, 0.999)), 1e-12)


def _clamped_equipment_limit(
    equipment_limit: float, process_limit: float, settings: Settings
) -> float:
    """설비별 한계를 공정 한계 대비 비율로 제한 (추정 잡음 방어)

    설비 표본은 공정 대비 훨씬 적어 q0.999 추정 분산이 큼, 진짜 개체 오프셋은 완만하므로
    비율 밖 값은 잡음으로 보고 잘라 과민·과둔감을 모두 차단
    """
    low = process_limit * settings.anomaly_calibration_min_ratio
    high = process_limit * settings.anomaly_calibration_max_ratio
    return min(max(equipment_limit, low), high)


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

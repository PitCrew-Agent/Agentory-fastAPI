"""이상 감지 서빙 데이터 접근 (BE_ANOM01_SERVE01)

모델 적재·저장, 설비별 최근 센서 시계열 조회, WRN-901 알람 발생/해제 전이
알람 발령은 기존 EquipmentAlarm에 기록해 sync_from_alarms → 알림 파이프라인 재사용
"""

from datetime import datetime

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.config import Settings
from agentory.modules.telemetry.models import EquipmentAlarm, EquipmentMaster, EquipmentTelemetry
from agentory.modules.watcher.detector import (
    ANOMALY_ALARM_CODE,
    ANOMALY_SEVERITY,
    VARS,
    AnomalyScorer,
    LoadedModel,
)
from agentory.modules.watcher.models import EquipmentAnomalyModel
from anomaly.models.pca_mspc import PcaMspc


async def save_model(
    session: AsyncSession,
    *,
    process_type: str,
    window: int,
    stride: int,
    confirm_k: int,
    ewma_limit: float,
    state: dict,
    train_rows: int,
) -> None:
    """공정 유형별 모델 upsert, 재적합 시 갱신 (drift 재캘리브레이션 대비)"""
    values = {
        "process_type": process_type,
        "window": window,
        "stride": stride,
        "confirm_k": confirm_k,
        "ewma_limit": ewma_limit,
        "state": state,
        "train_rows": train_rows,
    }
    stmt = pg_insert(EquipmentAnomalyModel).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[EquipmentAnomalyModel.process_type],
        set_={k: v for k, v in values.items() if k != "process_type"},
    )
    await session.execute(stmt)


async def load_scorer(session: AsyncSession, settings: Settings) -> AnomalyScorer:
    """저장된 전 모델을 적재해 스코어러 구성, EWMA 파라미터는 config에서 주입"""
    rows = await session.scalars(select(EquipmentAnomalyModel))
    models: dict[str, LoadedModel] = {}
    for row in rows:
        models[row.process_type] = LoadedModel(
            model=PcaMspc.from_state(row.state),
            window=row.window,
            stride=row.stride,
            confirm_k=row.confirm_k,
            ewma_alpha=settings.anomaly_ewma_alpha,
            ewma_clip=settings.anomaly_ewma_clip,
            ewma_limit=row.ewma_limit if row.ewma_limit is not None else 1.0,
        )
    return AnomalyScorer(models)


async def list_equipment(session: AsyncSession) -> list[tuple[str, str]]:
    """(equipment_id, process_type) 목록"""
    rows = await session.execute(select(EquipmentMaster.equipment_id, EquipmentMaster.process_type))
    return [(eid, ptype) for eid, ptype in rows]


async def fetch_recent_series(session: AsyncSession, equipment_id: str, limit: int) -> np.ndarray:
    """설비 최근 센서값을 시간 오름차순 (T, C) 배열로 조회, NULL 포함 행 제외"""
    columns = [getattr(EquipmentTelemetry, var) for var in VARS]
    rows = await session.execute(
        select(EquipmentTelemetry.timestamp, *columns)
        .where(EquipmentTelemetry.equipment_id == equipment_id)
        .order_by(EquipmentTelemetry.timestamp.desc())
        .limit(limit)
    )
    values = [[float(v) for v in row[1:]] for row in rows if all(v is not None for v in row[1:])]
    values.reverse()  # desc 조회를 시간 오름차순으로
    return np.array(values, dtype=float) if values else np.empty((0, len(VARS)))


async def active_anomaly_equipment(session: AsyncSession) -> set[str]:
    """열린 WRN-901 알람이 있는 설비 집합, 중복 발생·유령 해제 방지"""
    rows = await session.scalars(
        select(EquipmentAlarm.equipment_id).where(
            EquipmentAlarm.alarm_code == ANOMALY_ALARM_CODE,
            EquipmentAlarm.cleared_at.is_(None),
        )
    )
    return set(rows)


async def raise_anomaly(
    session: AsyncSession, equipment_id: str, metric: str, now: datetime
) -> None:
    """WRN-901 활성 알람 발생, 기여 채널을 metric으로 기록"""
    session.add(
        EquipmentAlarm(
            equipment_id=equipment_id,
            metric=metric,
            alarm_code=ANOMALY_ALARM_CODE,
            severity=ANOMALY_SEVERITY,
            raised_at=now,
        )
    )


async def clear_anomaly(session: AsyncSession, equipment_id: str, now: datetime) -> None:
    """설비의 열린 WRN-901 알람 해제"""
    await session.execute(
        update(EquipmentAlarm)
        .where(
            EquipmentAlarm.equipment_id == equipment_id,
            EquipmentAlarm.alarm_code == ANOMALY_ALARM_CODE,
            EquipmentAlarm.cleared_at.is_(None),
        )
        .values(cleared_at=now)
    )

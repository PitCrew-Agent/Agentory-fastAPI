"""알림 조회·동기화 레포지토리 (NEW_PROACT01_ALERT01)

변수별 알람 저널(EquipmentAlarm)을 notifications로 멱등 동기화(sync-on-read), 읽음 상태 갱신
동일 설비+변수+알람은 30분 버킷당 첫 알람 1건만 적재해 중복 억제 (NEW_PROACT01_ALERT03)
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.notification.messages import build_notification_message
from agentory.modules.notification.models import Notification
from agentory.modules.telemetry.models import EquipmentAlarm

# 30분 버킷 경계 기준점(00분·30분 정렬용), date_bin origin으로 사용
_BUCKET_ORIGIN = text("timestamptz '2000-01-01 00:00:00+00'")
_BUCKET_WIDTH = text("interval '30 minutes'")


def _to_dict(row: Notification) -> dict[str, Any]:
    # 알림 행을 응답·이벤트 공용 dict로 변환
    # message는 저장값 대신 코드에서 요청 로케일로 재빌드해 다국어 응답 (ko는 저장값과 동일)
    return {
        "id": row.id,
        "occurred_at": row.occurred_at,
        "equipment_id": row.equipment_id,
        "metric": row.metric,
        "alarm_code": row.alarm_code,
        "message": build_notification_message(row.equipment_id, row.alarm_code),
        "is_read": row.is_read,
    }


async def sync_from_alarms(session: AsyncSession) -> int:
    # 설비+변수+알람+30분버킷별 첫 알람만 알림화, 버킷당 1건 유니크로 멱등, 신규 적재 건수 반환
    bucket = func.date_bin(_BUCKET_WIDTH, EquipmentAlarm.raised_at, _BUCKET_ORIGIN)
    grouped = select(
        EquipmentAlarm.equipment_id.label("equipment_id"),
        EquipmentAlarm.metric.label("metric"),
        EquipmentAlarm.alarm_code.label("alarm_code"),
        bucket.label("bucket_start"),
        func.min(EquipmentAlarm.raised_at).label("occurred_at"),
        func.min(EquipmentAlarm.alarm_id).label("source_alarm_id"),
    ).group_by(
        EquipmentAlarm.equipment_id,
        EquipmentAlarm.metric,
        EquipmentAlarm.alarm_code,
        bucket,
    )
    groups = (await session.execute(grouped)).all()
    if not groups:
        return 0
    values = [
        {
            "occurred_at": g.occurred_at,
            "equipment_id": g.equipment_id,
            "metric": g.metric,
            "alarm_code": g.alarm_code,
            "bucket_start": g.bucket_start,
            "message": build_notification_message(g.equipment_id, g.alarm_code),
            "source_alarm_id": g.source_alarm_id,
        }
        for g in groups
    ]
    # 기존 버킷은 유니크 충돌로 무시, 신규 버킷만 적재
    stmt = (
        pg_insert(Notification)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_notifications_equip_metric_alarm_bucket")
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount or 0


async def fetch_notifications(
    session: AsyncSession,
    *,
    unread_only: bool = False,
    after_id: int | None = None,
) -> list[dict[str, Any]]:
    # after_id 지정 시 그보다 큰 id 오름차순(SSE 증분용), 아니면 발생 역순(이력용)
    stmt = select(Notification)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    if after_id is not None:
        stmt = stmt.where(Notification.id > after_id).order_by(Notification.id)
    else:
        stmt = stmt.order_by(Notification.occurred_at.desc(), Notification.id.desc())
    rows = await session.scalars(stmt)
    return [_to_dict(r) for r in rows]


async def fetch_notifications_page(
    session: AsyncSession,
    *,
    unread_only: bool = False,
    before: tuple[datetime, int] | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    # 발생 역순(occurred_at, id) 키셋 페이지네이션, before 커서보다 과거 항목만
    # 새 알림이 위에 쌓여도 경계가 밀리지 않도록 offset 대신 키셋 사용
    stmt = select(Notification)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    if before is not None:
        # row-value 튜플 비교로 커서, OR 펼침 대비 sargable 해 인덱스 커서 위치로 직접 seek
        # 깊은 페이지에서도 상수 시간, ix_notifications_occurred_at 그대로 활용
        stmt = stmt.where(tuple_(Notification.occurred_at, Notification.id) < before)
    stmt = stmt.order_by(Notification.occurred_at.desc(), Notification.id.desc()).limit(limit)
    rows = await session.scalars(stmt)
    return [_to_dict(r) for r in rows]


async def mark_read(session: AsyncSession, notification_id: int) -> bool:
    # 개별 읽음, 대상 없으면 False
    stmt = update(Notification).where(Notification.id == notification_id).values(is_read=True)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def mark_all_read(session: AsyncSession) -> int:
    # 미읽음 전체 읽음 처리, 갱신 건수 반환
    stmt = update(Notification).where(Notification.is_read.is_(False)).values(is_read=True)
    result = await session.execute(stmt)
    return result.rowcount

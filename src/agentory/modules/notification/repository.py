"""알림 조회·동기화 레포지토리 (NEW_PROACT01_ALERT01)

telemetry 알람을 notifications로 멱등 동기화(sync-on-read), 읽음 상태 갱신
"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.notification.messages import build_notification_message
from agentory.modules.notification.models import Notification
from agentory.modules.telemetry.models import EquipmentTelemetry


def _to_dict(row: Notification) -> dict[str, Any]:
    # 알림 행을 응답·이벤트 공용 dict로 변환
    return {
        "id": row.id,
        "occurred_at": row.occurred_at,
        "equipment_id": row.equipment_id,
        "alarm_code": row.alarm_code,
        "message": row.message,
        "is_read": row.is_read,
    }


async def sync_from_telemetry(session: AsyncSession) -> int:
    # 아직 알림화되지 않은 telemetry 알람을 notifications로 적재, 적재 건수 반환
    already = select(Notification.source_log_id).where(Notification.source_log_id.is_not(None))
    stmt = (
        select(EquipmentTelemetry)
        .where(
            EquipmentTelemetry.alarm_code.is_not(None),
            EquipmentTelemetry.log_id.not_in(already),
        )
        .order_by(EquipmentTelemetry.timestamp)
    )
    rows = (await session.scalars(stmt)).all()
    for row in rows:
        session.add(
            Notification(
                occurred_at=row.timestamp,
                equipment_id=row.equipment_id,
                alarm_code=row.alarm_code,
                message=build_notification_message(row.equipment_id, row.alarm_code),
                source_log_id=row.log_id,
            )
        )
    await session.flush()
    return len(rows)


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

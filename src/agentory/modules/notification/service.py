"""알림 서비스 (NEW_PROACT01_ALERT01)

조회·SSE 시 telemetry 알람을 동기화(sync-on-read)한 뒤 최신 알림 반환
쓰기 경로는 명시적 commit (get_session은 자동 커밋 안 함)
"""

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.notification import repository
from agentory.modules.notification.schemas import NotificationItem, ReadAllResponse


async def list_notifications(
    session: AsyncSession, unread_only: bool = False
) -> list[NotificationItem]:
    # sync-on-read로 신규 알람 반영 후 목록 반환
    await repository.sync_from_telemetry(session)
    await session.commit()
    rows = await repository.fetch_notifications(session, unread_only=unread_only)
    return [NotificationItem(**row) for row in rows]


async def mark_read(session: AsyncSession, notification_id: int) -> bool:
    # 개별 읽음 처리, 대상 존재 여부 반환
    updated = await repository.mark_read(session, notification_id)
    await session.commit()
    return updated


async def mark_all_read(session: AsyncSession) -> ReadAllResponse:
    # 일괄 읽음 처리
    count = await repository.mark_all_read(session)
    await session.commit()
    return ReadAllResponse(updated=count)

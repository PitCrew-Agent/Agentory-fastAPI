"""알림 서비스 (NEW_PROACT01_ALERT01)

조회·SSE 시 telemetry 알람을 동기화(sync-on-read)한 뒤 최신 알림 반환
목록은 발생 역순 키셋 커서 페이지네이션(NEW_PROACT01_ALERT02), 페이지당 기본 10개
쓰기 경로는 명시적 commit (get_session은 자동 커밋 안 함)
"""

import base64
import binascii
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.notification import repository
from agentory.modules.notification.schemas import (
    NotificationItem,
    NotificationPage,
    ReadAllResponse,
)

DEFAULT_PAGE_SIZE = 10  # 페이지당 알림 수 기본값
MAX_PAGE_SIZE = 50  # 과도한 요청 방지 상한


def _encode_cursor(occurred_at: datetime, notification_id: int) -> str:
    # 커서는 마지막 항목의 (발생시각, id)를 base64로 감싼 불투명 토큰
    raw = f"{occurred_at.isoformat()}|{notification_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    # 잘못된 커서는 ValueError (라우터에서 400)
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        occurred_str, id_str = raw.rsplit("|", 1)
        return datetime.fromisoformat(occurred_str), int(id_str)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"잘못된 커서: {cursor}") from exc


async def list_notifications(
    session: AsyncSession,
    *,
    unread_only: bool = False,
    before: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> NotificationPage:
    # 첫 페이지(before 없음)에서만 sync-on-read, 이후 더보기는 조회만 해 지연 최소화
    if before is None:
        await repository.sync_from_telemetry(session)
        await session.commit()

    cursor = _decode_cursor(before) if before else None
    page_size = max(1, min(limit, MAX_PAGE_SIZE))
    # 다음 페이지 존재 여부 판단 위해 한 개 더 조회
    rows = await repository.fetch_notifications_page(
        session, unread_only=unread_only, before=cursor, limit=page_size + 1
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = (
        _encode_cursor(items[-1]["occurred_at"], items[-1]["id"]) if has_more and items else None
    )
    return NotificationPage(
        items=[NotificationItem(**row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


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

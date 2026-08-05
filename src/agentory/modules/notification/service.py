"""알림 서비스 (NEW_PROACT01_ALERT01)

알람→알림 동기화는 백그라운드 워처가 전담, 조회 경로는 읽기만 수행 (NEW_PROACT01_DETECT01)
목록은 발생 역순 페이지 번호 페이지네이션(NEW_PROACT01_ALERT02), 페이지당 기본 10개
화면이 페이지 번호로 임의 이동하므로 총 건수·총 페이지 수를 함께 반환
쓰기 경로는 명시적 commit (get_session은 자동 커밋 안 함)
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.exceptions import ValidationError
from agentory.modules.notification import repository
from agentory.modules.notification.schemas import (
    AvailableDatesResponse,
    NotificationItem,
    NotificationPage,
    ReadAllResponse,
)

DEFAULT_PAGE_SIZE = 10  # 페이지당 알림 수 기본값
MAX_PAGE_SIZE = 50  # 과도한 요청 방지 상한
ADMIN_ROLE = "admin"  # 담당 라인 스코핑 예외, 전 라인 알림 조회 (BE_NOTI01_SCOPE01)


async def scope_line_names(session: AsyncSession, user: dict[str, Any]) -> list[str] | None:
    # 조회 스코프 산출, 관리자는 전체(None)·현장 담당자는 배정 라인만 (BE_NOTI01_SCOPE01)
    if user.get("role") == ADMIN_ROLE:
        return None
    return await repository.assigned_line_names(session, user["user_id"])


def _validate_range(start: datetime | None, end: datetime | None) -> None:
    # 반열림 구간 전제, 경계 역전(start >= end) 요청 조기 차단 (BE_NOTI01_RANGE01)
    if start is not None and end is not None and start >= end:
        raise ValidationError("error.notification.invalid_range")


async def list_notifications(
    session: AsyncSession,
    *,
    unread_only: bool = False,
    page: int = 1,
    limit: int = DEFAULT_PAGE_SIZE,
    line_names: list[str] | None = None,
    user_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> NotificationPage:
    # 알람→알림 동기화는 백그라운드 워처 전담, 조회는 읽기만 수행 (NEW_PROACT01_DETECT01)
    # 캘린더 선택 기간은 반열림 구간, 경계 역전 요청은 조기 차단 (BE_NOTI01_RANGE01)
    _validate_range(start, end)
    page_size = max(1, min(limit, MAX_PAGE_SIZE))
    # 총 건수는 화면의 페이지 번호 렌더용, 조회 조건과 동일 스코프로 집계
    total_items = await repository.count_notifications(
        session,
        unread_only=unread_only,
        line_names=line_names,
        user_id=user_id,
        start=start,
        end=end,
    )
    total_pages = -(-total_items // page_size)  # 올림 나눗셈
    # 마지막 페이지를 넘는 요청은 마지막 페이지로 보정, 빈 목록 대신 유효 페이지 반환
    current_page = max(1, min(page, total_pages)) if total_pages else 1
    rows = await repository.fetch_notifications_page(
        session,
        unread_only=unread_only,
        offset=(current_page - 1) * page_size,
        limit=page_size,
        line_names=line_names,
        user_id=user_id,
        start=start,
        end=end,
    )
    return NotificationPage(
        items=[NotificationItem(**row) for row in rows],
        page=current_page,
        limit=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_more=current_page < total_pages,
    )


async def list_available_dates(
    session: AsyncSession,
    *,
    line_names: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> AvailableDatesResponse:
    # 캘린더 선택 가능 날짜, 알림이 있는 KST 날짜만 반환 (BE_NOTI01_RANGE01)
    _validate_range(start, end)
    dates = await repository.fetch_available_dates(
        session, line_names=line_names, start=start, end=end
    )
    return AvailableDatesResponse(dates=dates)


async def mark_read(
    session: AsyncSession,
    notification_id: int,
    user_id: int,
    *,
    line_names: list[str] | None = None,
) -> bool:
    # 개별 읽음 처리, 담당 라인 안의 대상 존재 여부 반환
    updated = await repository.mark_read(session, notification_id, user_id, line_names=line_names)
    await session.commit()
    return updated


async def mark_unread(
    session: AsyncSession,
    notification_id: int,
    user_id: int,
    *,
    line_names: list[str] | None = None,
) -> bool:
    # 개별 읽음 해제, 담당 라인 안의 대상 존재 여부 반환
    updated = await repository.mark_unread(session, notification_id, user_id, line_names=line_names)
    await session.commit()
    return updated


async def mark_all_read(
    session: AsyncSession, user_id: int, *, line_names: list[str] | None = None
) -> ReadAllResponse:
    # 담당 라인 일괄 읽음 처리
    count = await repository.mark_all_read(session, user_id, line_names=line_names)
    await session.commit()
    return ReadAllResponse(updated=count)

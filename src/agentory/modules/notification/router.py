"""알림 REST + 실시간 SSE (NEW_PROACT01_ALERT01)

목록·읽음은 REST, 실시간 전달은 SSE 스트림
스트림은 sync-on-poll로 신규 알림만 증분 방출, 커넥션 루프가 주기 잡 대체
SSE 이벤트 스키마는 agentory.common.events.NotificationEvent가 단일 소스
"""

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from agentory.common.events import NotificationEvent
from agentory.common.exceptions import NotFoundError
from agentory.common.response import ApiResponse
from agentory.core.db import SessionLocal, get_session
from agentory.modules.auth.middleware import get_current_user
from agentory.modules.notification import repository, service
from agentory.modules.notification.schemas import (
    AvailableDatesResponse,
    NotificationPage,
    ReadAllResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# SSE 폴링 간격(초), 인입 대비 이 지연으로 유사 실시간
STREAM_POLL_SECONDS = 3.0


@router.get(
    "",
    response_model=ApiResponse[NotificationPage],
    summary="알림 목록 조회 (페이지 번호 페이지네이션)",
)
async def list_notifications(
    unread_only: bool = Query(default=False, examples=[False]),
    page: int = Query(default=1, ge=1, description="조회할 페이지 번호 (1부터)", examples=[1]),
    limit: int = Query(
        default=service.DEFAULT_PAGE_SIZE, ge=1, le=service.MAX_PAGE_SIZE, examples=[10]
    ),
    start: datetime | None = Query(
        default=None,
        description="조회 시작 시각 포함(ISO 8601, tz 포함), 캘린더 선택 기간 하한",
        examples=["2026-08-01T00:00:00+09:00"],
    ),
    end: datetime | None = Query(
        default=None,
        description="조회 종료 시각 미포함(ISO 8601, tz 포함), 캘린더 선택 기간 상한(반열림)",
        examples=["2026-08-06T00:00:00+09:00"],
    ),
    session: AsyncSession = Depends(get_session),
    user: dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[NotificationPage]:
    """담당 라인 알림 목록, 발생 역순 페이지 번호 페이지네이션(기본 10개)

    start·end 지정 시 발생 시각 반열림 구간 [start, end) 필터, 미지정 시 전체 조회
    """
    line_names = await service.scope_line_names(session, user)
    result = await service.list_notifications(
        session,
        unread_only=unread_only,
        page=page,
        limit=limit,
        line_names=line_names,
        user_id=user["user_id"],
        start=start,
        end=end,
    )
    return ApiResponse.ok(result)


@router.get(
    "/available-dates",
    response_model=ApiResponse[AvailableDatesResponse],
    summary="알림 존재 날짜 조회 (캘린더 선택 가능 날짜)",
)
async def available_dates(
    start: datetime | None = Query(
        default=None,
        description="조회 시작 시각 포함(ISO 8601, tz 포함), 가시 월 하한",
        examples=["2026-08-01T00:00:00+09:00"],
    ),
    end: datetime | None = Query(
        default=None,
        description="조회 종료 시각 미포함(ISO 8601, tz 포함), 가시 월 상한(반열림)",
        examples=["2026-09-01T00:00:00+09:00"],
    ),
    session: AsyncSession = Depends(get_session),
    user: dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[AvailableDatesResponse]:
    """담당 라인 알림이 존재하는 KST 날짜 목록, 캘린더 선택 가능 날짜 하이라이트용

    start·end 지정 시 그 반열림 구간 [start, end)만 스캔, 미지정 시 전체 이력 대상
    """
    line_names = await service.scope_line_names(session, user)
    result = await service.list_available_dates(
        session, line_names=line_names, start=start, end=end
    )
    return ApiResponse.ok(result)


@router.post(
    "/read-all", response_model=ApiResponse[ReadAllResponse], summary="알림 일괄 읽음 처리"
)
async def read_all(
    session: AsyncSession = Depends(get_session),
    user: dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[ReadAllResponse]:
    """담당 라인 미읽음 알림 일괄 읽음 처리"""
    line_names = await service.scope_line_names(session, user)
    return ApiResponse.ok(
        await service.mark_all_read(session, user["user_id"], line_names=line_names)
    )


@router.patch(
    "/{notification_id}/read",
    response_model=ApiResponse[None],
    summary="알림 개별 읽음 처리",
    responses={404: {"description": "해당 알림이 존재하지 않음"}},
)
async def read_one(
    notification_id: int = Path(examples=[1024]),
    session: AsyncSession = Depends(get_session),
    user: dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[None]:
    """알림 개별 읽음 처리, 담당 라인 밖 알림은 404"""
    line_names = await service.scope_line_names(session, user)
    if not await service.mark_read(
        session, notification_id, user["user_id"], line_names=line_names
    ):
        raise NotFoundError("error.notification.not_found", params={"id": notification_id})
    return ApiResponse.ok()


@router.delete(
    "/{notification_id}/read",
    response_model=ApiResponse[None],
    summary="알림 개별 읽음 해제",
    responses={404: {"description": "해당 알림이 존재하지 않음"}},
)
async def unread_one(
    notification_id: int = Path(examples=[1024]),
    session: AsyncSession = Depends(get_session),
    user: dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[None]:
    """알림 개별 읽음 해제, 담당 라인 밖 알림은 404"""
    line_names = await service.scope_line_names(session, user)
    if not await service.mark_unread(
        session, notification_id, user["user_id"], line_names=line_names
    ):
        raise NotFoundError("error.notification.not_found", params={"id": notification_id})
    return ApiResponse.ok()


@router.get(
    "/stream",
    summary="신규 알림 실시간 스트림 (SSE)",
    responses={
        200: {
            "description": "text/event-stream, event=notification 으로 신규 알림을 순차 전송",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream(
    after_id: int = Query(default=0, description="이 id 이후 신규 알림만 수신", examples=[0]),
    user: dict[str, Any] = Depends(get_current_user),
) -> EventSourceResponse:
    """담당 라인 신규 알림 실시간 SSE 스트림 (event=notification)"""
    # 담당 라인은 접속 시점에 1회 확정, 스트림 수명 동안 DB 커넥션을 잡지 않도록 단기 세션 사용
    async with SessionLocal() as scope_session:
        line_names = await service.scope_line_names(scope_session, user)

    async def event_generator():
        last_id = after_id
        while True:
            # 알람 동기화는 백그라운드 워처 전담, 스트림은 신규 알림 조회만 (NEW_PROACT01_DETECT01)
            async with SessionLocal() as session:
                new = await repository.fetch_notifications(
                    session, after_id=last_id, line_names=line_names, user_id=user["user_id"]
                )
            for row in new:
                last_id = row["id"]
                event = NotificationEvent(
                    id=row["id"],
                    occurred_at=row["occurred_at"].isoformat(),
                    equipment_id=row["equipment_id"],
                    line_name=row["line_name"],
                    metric=row["metric"],
                    alarm_code=row["alarm_code"],
                    severity=row["severity"],
                    message=row["message"],
                    is_read=row["is_read"],
                )
                yield {"event": "notification", "data": event.model_dump_json()}
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return EventSourceResponse(event_generator())

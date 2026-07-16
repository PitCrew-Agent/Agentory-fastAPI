"""알림 REST + 실시간 SSE (NEW_PROACT01_ALERT01)

목록·읽음은 REST, 실시간 전달은 SSE 스트림
스트림은 sync-on-poll로 신규 알림만 증분 방출, 커넥션 루프가 주기 잡 대체
SSE 이벤트 스키마는 agentory.common.events.NotificationEvent가 단일 소스
"""

import asyncio

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from agentory.common.events import NotificationEvent
from agentory.common.exceptions import NotFoundError
from agentory.common.response import ApiResponse
from agentory.core.db import SessionLocal, get_session
from agentory.modules.notification import repository, service
from agentory.modules.notification.schemas import NotificationPage, ReadAllResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])

# SSE 폴링 간격(초), 인입 대비 이 지연으로 유사 실시간
STREAM_POLL_SECONDS = 3.0


@router.get(
    "",
    response_model=ApiResponse[NotificationPage],
    summary="알림 목록 조회 (커서 페이지네이션)",
    responses={400: {"description": "before 커서 형식이 잘못됨"}},
)
async def list_notifications(
    unread_only: bool = Query(default=False, examples=[False]),
    before: str | None = Query(
        default=None,
        description="이전 페이지 마지막 항목 커서, 첫 페이지는 생략",
        examples=["MjAyNi0wNy0xMFQxNTo0MzoyNSswOTowMHwxMDI0"],
    ),
    limit: int = Query(
        default=service.DEFAULT_PAGE_SIZE, ge=1, le=service.MAX_PAGE_SIZE, examples=[10]
    ),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[NotificationPage]:
    """설비 알람 알림 목록, 발생 역순 커서 페이지네이션(기본 10개)"""
    # 잘못된 커서는 서비스가 ValidationError raise, 전역 핸들러가 400 통일 응답
    page = await service.list_notifications(
        session, unread_only=unread_only, before=before, limit=limit
    )
    return ApiResponse.ok(page)


@router.post(
    "/read-all", response_model=ApiResponse[ReadAllResponse], summary="알림 일괄 읽음 처리"
)
async def read_all(session: AsyncSession = Depends(get_session)) -> ApiResponse[ReadAllResponse]:
    """미읽음 알림 일괄 읽음 처리"""
    return ApiResponse.ok(await service.mark_all_read(session))


@router.patch(
    "/{notification_id}/read",
    response_model=ApiResponse[None],
    summary="알림 개별 읽음 처리",
    responses={404: {"description": "해당 알림이 존재하지 않음"}},
)
async def read_one(
    notification_id: int = Path(examples=[1024]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[None]:
    """알림 개별 읽음 처리"""
    if not await service.mark_read(session, notification_id):
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
) -> EventSourceResponse:
    """신규 알림 실시간 SSE 스트림 (event=notification)"""

    async def event_generator():
        last_id = after_id
        while True:
            # 커넥션 루프에서 sync-on-poll, 세션은 매 반복 새로 열고 닫음
            async with SessionLocal() as session:
                await repository.sync_from_alarms(session)
                await session.commit()
                new = await repository.fetch_notifications(session, after_id=last_id)
            for row in new:
                last_id = row["id"]
                event = NotificationEvent(
                    id=row["id"],
                    occurred_at=row["occurred_at"].isoformat(),
                    equipment_id=row["equipment_id"],
                    metric=row["metric"],
                    alarm_code=row["alarm_code"],
                    severity=row["severity"],
                    message=row["message"],
                    is_read=row["is_read"],
                )
                yield {"event": "notification", "data": event.model_dump_json()}
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return EventSourceResponse(event_generator())

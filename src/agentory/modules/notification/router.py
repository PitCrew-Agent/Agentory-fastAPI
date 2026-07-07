"""알림 REST + 실시간 SSE (NEW_PROACT01_ALERT01)

목록·읽음은 REST, 실시간 전달은 SSE 스트림
스트림은 sync-on-poll로 신규 알림만 증분 방출, 커넥션 루프가 주기 잡 대체
SSE 이벤트 스키마는 agentory.common.events.NotificationEvent가 단일 소스
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from agentory.common.events import NotificationEvent
from agentory.core.db import SessionLocal, get_session
from agentory.modules.notification import repository, service
from agentory.modules.notification.schemas import NotificationItem, ReadAllResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])

# SSE 폴링 간격(초), 인입 대비 이 지연으로 유사 실시간
STREAM_POLL_SECONDS = 3.0


@router.get("", response_model=list[NotificationItem])
async def list_notifications(
    unread_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[NotificationItem]:
    # 알림 이력 목록 (sync-on-read), unread_only로 미읽음만
    return await service.list_notifications(session, unread_only=unread_only)


@router.post("/read-all", response_model=ReadAllResponse)
async def read_all(session: AsyncSession = Depends(get_session)) -> ReadAllResponse:
    # 일괄 읽음 처리
    return await service.mark_all_read(session)


@router.patch("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_one(
    notification_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    # 개별 읽음, 미존재면 404
    if not await service.mark_read(session, notification_id):
        raise HTTPException(status_code=404, detail=f"알림 없음: {notification_id}")


@router.get("/stream")
async def stream(after_id: int = 0) -> EventSourceResponse:
    # 신규 알림 실시간 SSE, after_id 이후부터 방출
    async def event_generator():
        last_id = after_id
        while True:
            # 커넥션 루프에서 sync-on-poll, 세션은 매 반복 새로 열고 닫음
            async with SessionLocal() as session:
                await repository.sync_from_telemetry(session)
                await session.commit()
                new = await repository.fetch_notifications(session, after_id=last_id)
            for row in new:
                last_id = row["id"]
                event = NotificationEvent(
                    id=row["id"],
                    occurred_at=row["occurred_at"].isoformat(),
                    equipment_id=row["equipment_id"],
                    alarm_code=row["alarm_code"],
                    message=row["message"],
                    is_read=row["is_read"],
                )
                yield {"event": "notification", "data": event.model_dump_json()}
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return EventSourceResponse(event_generator())

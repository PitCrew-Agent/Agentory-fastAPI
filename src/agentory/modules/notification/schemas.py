"""알림 응답 스키마 (NEW_PROACT01_ALERT01)"""

from datetime import datetime

from pydantic import BaseModel


class NotificationItem(BaseModel):
    # 알림 목록 항목 (알림 이력 페이지·상단 벨)
    id: int
    occurred_at: datetime
    equipment_id: str
    alarm_code: str
    message: str
    is_read: bool


class NotificationPage(BaseModel):
    # 알림 목록 한 페이지 (커서 기반), next_cursor로 다음 10개 요청
    items: list[NotificationItem]
    next_cursor: str | None = None  # 다음 페이지 요청용 커서, 더 없으면 None
    has_more: bool  # 다음 페이지 존재 여부


class ReadAllResponse(BaseModel):
    # 일괄 읽음 처리 결과
    updated: int

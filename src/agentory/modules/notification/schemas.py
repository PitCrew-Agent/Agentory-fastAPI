"""알림 응답 스키마 (NEW_PROACT01_ALERT01)"""

from datetime import datetime

from pydantic import BaseModel, Field


class NotificationItem(BaseModel):
    # 알림 목록 항목 (알림 이력 페이지·상단 벨)
    id: int = Field(description="알림 고유 id", examples=[1024])
    occurred_at: datetime = Field(
        description="알람 발생 시각 (ISO 8601)", examples=["2026-07-10T15:43:25+09:00"]
    )
    equipment_id: str = Field(description="알람이 발생한 설비 id", examples=["EQP-A01"])
    metric: str | None = Field(
        default=None,
        description="알람이 발생한 센서 변수 키, 변수 특정 불가 시 null",
        examples=["temperature"],
    )
    alarm_code: str = Field(description="알람 코드", examples=["ERR-402"])
    message: str = Field(description="사용자 표시 알림 메시지", examples=["EQP-A01 압력 상한 초과"])
    is_read: bool = Field(description="읽음 여부", examples=[False])


class NotificationPage(BaseModel):
    # 알림 목록 한 페이지 (커서 기반), next_cursor로 다음 10개 요청
    items: list[NotificationItem] = Field(description="이번 페이지의 알림 목록 (발생 역순)")
    next_cursor: str | None = Field(
        default=None,
        description="다음 페이지 요청 시 before에 넣을 커서, 더 없으면 null",
        examples=["MjAyNi0wNy0xMFQxNTo0MzoyNSswOTowMHwxMDI0"],
    )
    has_more: bool = Field(description="다음 페이지 존재 여부", examples=[True])


class ReadAllResponse(BaseModel):
    # 일괄 읽음 처리 결과
    updated: int = Field(description="읽음으로 바뀐 알림 수", examples=[7])

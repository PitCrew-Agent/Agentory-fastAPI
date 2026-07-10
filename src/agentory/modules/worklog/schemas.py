"""작업 로그 요청/응답 스키마 (NEW_LOOP01_WORKLOG01)

진행자·소유자는 로그인 사용자에서 자동 기록하므로 요청 body에 없음
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class WorkLogStatus(StrEnum):
    # 작업 진행 상태, 프론트 배지와 동일 용어
    PENDING = "대기"
    IN_PROGRESS = "진행중"
    DONE = "완료"


class WorkLogCreate(BaseModel):
    # 작업 로그 작성 (시작~종료 범위·내용·상태), 진행자는 로그인 사용자에서 자동
    started_at: datetime = Field(
        description="작업 시작 시각", examples=["2026-07-10T09:00:00+09:00"]
    )
    ended_at: datetime | None = Field(
        default=None,
        description="작업 종료 시각, 진행중이면 생략",
        examples=["2026-07-10T11:30:00+09:00"],
    )
    content: str = Field(
        min_length=1, description="작업 내용", examples=["EQP-A05 챔버 압력 센서 교체"]
    )
    status: WorkLogStatus = Field(
        default=WorkLogStatus.PENDING, description="진행 상태", examples=["진행중"]
    )

    @model_validator(mode="after")
    def _check_range(self) -> "WorkLogCreate":
        # 종료가 있으면 시작 이후여야 함
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at은 started_at 이후여야 합니다")
        return self


class WorkLogUpdate(BaseModel):
    # 부분 수정, 전달된 필드만 반영 (상태 변경 등)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    content: str | None = Field(default=None, min_length=1)
    status: WorkLogStatus | None = None


class WorkLogItem(BaseModel):
    # 작업 로그 목록·상세 항목 (owner_sub는 노출 안 함)
    id: int = Field(description="작업 로그 id", examples=[15])
    worker_name: str = Field(description="진행자 이름 (작성자)", examples=["홍길동"])
    started_at: datetime = Field(description="작업 시작 시각")
    ended_at: datetime | None = Field(default=None, description="작업 종료 시각, 진행중이면 null")
    content: str = Field(description="작업 내용")
    status: WorkLogStatus = Field(description="진행 상태", examples=["완료"])
    created_at: datetime = Field(description="로그 생성 시각")

"""관리자 라인·유저 요청/응답 스키마 (BE_ADMIN01_LINE01)

담당 라인은 항상 리스트로 노출, 유저 조회 시 부서 대신 담당 라인 사용
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class LineStatus(StrEnum):
    # 라인 운영 상태, 삭제 대신 비활성 처리에도 사용
    ACTIVE = "active"
    INACTIVE = "inactive"


class LineRef(BaseModel):
    # 유저 응답에 실리는 담당 라인 요약, 여러 개면 리스트
    id: int
    code: str
    name: str


class LineCreate(BaseModel):
    # 라인 생성, code는 설비 line_name과 매칭되는 고유값
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    display_order: int | None = None


class LineUpdate(BaseModel):
    # 부분 수정, 전달된 필드만 반영
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    display_order: int | None = None
    status: LineStatus | None = None


class LineItem(BaseModel):
    # 라인 목록·상세 항목
    id: int
    code: str
    name: str
    description: str | None = None
    display_order: int | None = None
    status: LineStatus
    created_at: datetime
    updated_at: datetime


class AssignLinesRequest(BaseModel):
    # 유저 담당 라인 전체 교체, 빈 리스트면 전부 해제
    line_ids: list[int] = Field(default_factory=list)


class AdminUserItem(BaseModel):
    # 관리자 유저 목록·상세, 부서 대신 담당 라인 리스트 노출
    id: int
    email: str
    name: str
    role: str
    status: str
    lines: list[LineRef] = Field(default_factory=list)

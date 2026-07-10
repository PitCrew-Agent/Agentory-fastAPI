"""관리자 라인·유저 요청/응답 스키마 (BE_ADMIN01_LINE01)

담당 라인은 항상 리스트로 노출, 유저 조회 시 부서 대신 담당 라인 사용
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from agentory.modules.telemetry.schemas import EquipmentManager


class LineStatus(StrEnum):
    # 라인 운영 상태, 삭제 대신 비활성 처리에도 사용
    ACTIVE = "active"
    INACTIVE = "inactive"


class LineRef(BaseModel):
    # 유저 응답에 실리는 담당 라인 요약, 여러 개면 리스트
    id: int = Field(description="라인 id", examples=[3])
    code: str = Field(description="라인 코드 (설비 line_name과 매칭)", examples=["B-Line"])
    name: str = Field(description="라인 표시명", examples=["B 라인"])


class LineCreate(BaseModel):
    # 라인 생성, code는 설비 line_name과 매칭되는 고유값
    code: str = Field(
        min_length=1, max_length=50, description="고유 라인 코드", examples=["B-Line"]
    )
    name: str = Field(min_length=1, max_length=100, description="라인 표시명", examples=["B 라인"])
    description: str | None = Field(default=None, description="설명", examples=["식각 공정 B 라인"])
    display_order: int | None = Field(default=None, description="목록 정렬 순서", examples=[2])


class LineUpdate(BaseModel):
    # 부분 수정, 전달된 필드만 반영
    code: str | None = Field(default=None, min_length=1, max_length=50, description="라인 코드")
    name: str | None = Field(default=None, min_length=1, max_length=100, description="라인 표시명")
    description: str | None = Field(default=None, description="설명")
    display_order: int | None = Field(default=None, description="목록 정렬 순서")
    status: LineStatus | None = Field(default=None, description="active 또는 inactive")


class LineItem(BaseModel):
    # 라인 목록·상세 항목
    id: int = Field(description="라인 id", examples=[3])
    code: str = Field(description="라인 코드", examples=["B-Line"])
    name: str = Field(description="라인 표시명", examples=["B 라인"])
    description: str | None = Field(default=None, description="설명")
    display_order: int | None = Field(default=None, description="목록 정렬 순서", examples=[2])
    status: LineStatus = Field(description="운영 상태", examples=["active"])
    created_at: datetime = Field(description="생성 시각")
    updated_at: datetime = Field(description="수정 시각")


class AssignLinesRequest(BaseModel):
    # 유저 담당 라인 전체 교체, 빈 리스트면 전부 해제
    line_ids: list[int] = Field(
        default_factory=list,
        description="지정할 라인 id 목록, 빈 배열이면 전부 해제",
        examples=[[1, 3]],
    )


class AdminUserItem(BaseModel):
    # 관리자 유저 목록·상세, 부서 대신 담당 라인 리스트 노출
    id: int = Field(description="유저 id", examples=[7])
    email: str = Field(description="이메일", examples=["hong@example.com"])
    name: str = Field(description="이름", examples=["홍길동"])
    role: str = Field(description="권한 (admin·field_engineer)", examples=["field_engineer"])
    status: str = Field(description="계정 상태", examples=["active"])
    lines: list[LineRef] = Field(default_factory=list, description="담당 라인 목록 (부서 대체)")


class AssignManagerRequest(BaseModel):
    # 설비 책임자 지정, None이면 책임자 해제
    user_id: int | None = Field(
        default=None, description="책임자로 지정할 유저 id, null이면 해제", examples=[7]
    )


class EquipmentManagerItem(BaseModel):
    # 설비 책임자 지정 결과, 미지정 시 manager는 None
    equipment_id: str = Field(description="설비 id", examples=["EQP-A01"])
    manager: EquipmentManager | None = Field(
        default=None, description="지정된 책임자 유저, 미지정 시 null"
    )

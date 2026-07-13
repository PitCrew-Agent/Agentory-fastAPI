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


class RepairRequest(BaseModel):
    # 설비 수리 처리 요청, 수리자는 로그인 유저로 자동 기록
    note: str | None = Field(
        default=None, description="수리 비고", examples=["냉각 라인 교체 후 정상 확인"]
    )


class RepairItem(BaseModel):
    # 수리 이력 항목 (작업 현황·장비별 이력)
    id: int = Field(description="수리 이력 id", examples=[42])
    equipment_id: str = Field(description="수리한 설비 id", examples=["EQP-A05"])
    repaired_by: int | None = Field(
        default=None, description="수리 책임자 유저 id, 유저 삭제 시 null", examples=[7]
    )
    repaired_by_name: str | None = Field(
        default=None, description="수리 책임자 이름, 미지정 시 null", examples=["김억산"]
    )
    repaired_at: datetime = Field(
        description="수리 시각 (ISO 8601)", examples=["2026-07-13T10:15:00+09:00"]
    )
    alarm_code_before: str | None = Field(
        default=None, description="수리 직전 알람 코드, 없으면 null", examples=["ERR-402"]
    )
    note: str | None = Field(default=None, description="수리 비고", examples=["냉각 라인 교체"])


class RepairPage(BaseModel):
    # 수리 이력 한 페이지 (커서 기반), next_cursor로 다음 페이지 요청
    items: list[RepairItem] = Field(description="이번 페이지의 수리 이력 (수리 역순)")
    next_cursor: str | None = Field(
        default=None,
        description="다음 페이지 요청 시 before에 넣을 커서, 더 없으면 null",
        examples=["MjAyNi0wNy0xM1QxMDoxNTowMCswOTowMHw0Mg"],
    )
    has_more: bool = Field(description="다음 페이지 존재 여부", examples=[True])

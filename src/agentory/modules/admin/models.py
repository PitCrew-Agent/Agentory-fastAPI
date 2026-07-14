"""라인 마스터·유저 담당 라인 모델 (BE_ADMIN01_LINE01)

lines는 담당 라인의 단일 원천, code가 설비 line_name과 이어지는 연결 고리
user_lines는 유저 N : 라인 N 담당 관계, 관리자만 지정
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class Line(Base):
    """담당 라인 마스터, 관리자 CRUD 대상"""

    __tablename__ = "lines"
    __table_args__ = (CheckConstraint("status IN ('active', 'inactive')", name="ck_lines_status"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True
    )  # 설비 line_name과 매칭
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 표시명 (예: B 라인)
    description: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int | None] = mapped_column(Integer)  # 라인 목록 정렬 순서
    status: Mapped[str] = mapped_column(String(20), server_default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EquipmentRepair(Base):
    """설비 수리 이력 (NEW_REPAIR01_HISTORY01)

    누가·언제·어떤 설비를 수리했는지 영속, 작업 현황 조회와 재정비 에이전트(타 팀)의 원천
    수리 시 설비를 힐 윈도우 동안 정상 강제(NEW_REPAIR01_SIM01), 직전 알람 코드도 함께 스냅샷
    """

    __tablename__ = "equipment_repairs"
    __table_args__ = (
        Index("ix_equipment_repairs_equip_time", "equipment_id", "repaired_at"),
        Index("ix_equipment_repairs_repaired_by", "repaired_by"),
        # 설비 미지정 전역 페이지 정렬용 (NEW_REPAIR01_HISTORY01), row-value 커서 seek 지원
        Index("ix_equipment_repairs_repaired_at_id", "repaired_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("equipment_masters.equipment_id"), nullable=False
    )
    # 수리 책임자 유저, 유저 삭제 시 이력은 유지하고 참조만 해제
    repaired_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    repaired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    alarm_code_before: Mapped[str | None] = mapped_column(String(20))  # 수리 직전 알람 스냅샷
    note: Mapped[str | None] = mapped_column(Text)  # 수리 비고
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserLine(Base):
    """유저 담당 라인 연결 (M2M), 유저·라인 삭제 시 연쇄 정리"""

    __tablename__ = "user_lines"
    __table_args__ = (
        UniqueConstraint("user_id", "line_id", name="uq_user_lines_user_line"),
        Index("ix_user_lines_user_id", "user_id"),
        Index("ix_user_lines_line_id", "line_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    line_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lines.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

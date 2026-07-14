"""작업 로그 모델 (NEW_LOOP01_WORKLOG01)

현장 점검·조치 기록, 작성자=소유자(owner_sub)만 수정·삭제 가능
삭제는 soft delete(deleted_at), 작업 시간은 시작~종료 범위(종료는 진행중 미정 허용)
진행자 이름은 로그인 사용자에서 자동 기록(표시용), 상태는 대기/진행중/완료 제약
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class WorkLog(Base):
    """작업 로그 항목"""

    __tablename__ = "work_logs"
    __table_args__ = (
        CheckConstraint("status IN ('대기', '진행중', '완료')", name="ck_work_logs_status"),
        CheckConstraint(
            "work_type IN ('정기점검', '수리점검', '예방점검', '긴급수리', '기타')",
            name="ck_work_logs_work_type",
        ),
        # 미삭제 목록을 시작 시각 역순으로 조회하는 패턴 대응
        Index(
            "ix_work_logs_active_started",
            "started_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_work_logs_owner", "owner_sub"),
        Index(
            "ix_work_logs_source_notification_id",
            "source_notification_id",
        ),
        Index("ix_work_logs_equipment_id", "equipment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    owner_sub: Mapped[str] = mapped_column(String(255), nullable=False)  # 작성자=소유자 sub
    work_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 작업 유형(단일 선택)
    worker_name: Mapped[str] = mapped_column(String(100), nullable=False)  # 진행자 표시명
    source_notification_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("notifications.id"),
        nullable=True,
    )
    equipment_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("equipment_masters.equipment_id"),
        nullable=True,
    )
    alarm_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 진행중이면 NULL
    plan: Mapped[str] = mapped_column(Text, nullable=False)  # 작업 계획 (작성 시 필수)
    completion: Mapped[str | None] = mapped_column(Text)  # 작업 완료 내용, 완료 시 작성
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 완료 제출 시각
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="대기")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

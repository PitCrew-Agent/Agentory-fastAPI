"""work_logs 테이블 추가 (NEW_LOOP01_WORKLOG01)

Revision ID: 0006_work_logs
Revises: 0005_notifications
Create Date: 2026-07-07

현장 작업 로그 기록용, 소유자(owner_sub)만 수정·삭제, 삭제는 soft delete
작업 시간은 시작~종료 범위(종료는 진행중 미정 허용), 상태는 대기/진행중/완료 제약
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_work_logs"
down_revision: str | None = "0005_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("owner_sub", sa.String(255), nullable=False),  # 작성자=소유자 Keycloak sub
        sa.Column("worker_name", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default="대기", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # soft delete 표시
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('대기', '진행중', '완료')", name="ck_work_logs_status"),
    )
    # 미삭제 목록을 시간 역순으로 조회하는 패턴 대응
    op.create_index(
        "ix_work_logs_active_started",
        "work_logs",
        ["started_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_work_logs_owner", "work_logs", ["owner_sub"])


def downgrade() -> None:
    op.drop_index("ix_work_logs_owner", table_name="work_logs")
    op.drop_index("ix_work_logs_active_started", table_name="work_logs")
    op.drop_table("work_logs")

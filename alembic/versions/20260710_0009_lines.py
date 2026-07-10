"""lines 마스터·user_lines 담당 라인 테이블 추가 (BE_ADMIN01_LINE01)

Revision ID: 0009_lines
Revises: 0008_alarm_latch
Create Date: 2026-07-10

담당 라인의 단일 원천 lines, 유저 담당 라인 연결 user_lines
code는 설비 line_name과 매칭되는 고유값, 유저·라인 삭제 시 연결 연쇄 정리
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_lines"
down_revision: str | None = "0008_alarm_latch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),  # 설비 line_name과 매칭
        sa.Column("name", sa.String(100), nullable=False),  # 표시명
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=True),  # 라인 목록 정렬 순서
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_lines_status"),
        sa.UniqueConstraint("code", name="uq_lines_code"),
    )

    op.create_table(
        "user_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("line_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_lines_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["line_id"], ["lines.id"], name="fk_user_lines_line", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", "line_id", name="uq_user_lines_user_line"),
    )
    op.create_index("ix_user_lines_user_id", "user_lines", ["user_id"])
    op.create_index("ix_user_lines_line_id", "user_lines", ["line_id"])


def downgrade() -> None:
    op.drop_index("ix_user_lines_line_id", table_name="user_lines")
    op.drop_index("ix_user_lines_user_id", table_name="user_lines")
    op.drop_table("user_lines")
    op.drop_table("lines")

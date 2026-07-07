"""notifications 테이블 추가 및 head 병합 (NEW_PROACT01_ALERT01)

Revision ID: 0005_notifications
Revises: 0003_pluralize_telemetry, 0004_audit_log_indexes
Create Date: 2026-07-07

두 head(0003_pluralize_telemetry, 0004_audit_log_indexes) 병합 겸 알림 테이블 추가
알림 이력·실시간 SSE용, source_log_id로 telemetry 알람 멱등 동기화(watcher 알림은 NULL)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_notifications"
down_revision: str | Sequence[str] | None = (
    "0003_pluralize_telemetry",
    "0004_audit_log_indexes",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equipment_id", sa.String(50), nullable=False),
        sa.Column("alarm_code", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source_log_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("source_log_id", name="uq_notifications_source_log"),
    )
    op.create_index("ix_notifications_occurred_at", "notifications", ["occurred_at"])
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])


def downgrade() -> None:
    op.drop_index("ix_notifications_is_read", table_name="notifications")
    op.drop_index("ix_notifications_occurred_at", table_name="notifications")
    op.drop_table("notifications")

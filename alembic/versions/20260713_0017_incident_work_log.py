"""작업 로그 알림 연결 필드 추가

Revision ID: 0017_incident_work_log
Revises: 0016_work_log_type
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_incident_work_log"
down_revision: str | Sequence[str] | None = "0016_work_log_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "work_logs",
        sa.Column("source_notification_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "work_logs",
        sa.Column("equipment_id", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "work_logs",
        sa.Column("alarm_code", sa.String(length=20), nullable=True),
    )
    op.create_foreign_key(
        "fk_work_logs_source_notification_id_notifications",
        "work_logs",
        "notifications",
        ["source_notification_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_work_logs_equipment_id_equipment_masters",
        "work_logs",
        "equipment_masters",
        ["equipment_id"],
        ["equipment_id"],
    )
    op.create_index(
        "ix_work_logs_source_notification_id",
        "work_logs",
        ["source_notification_id"],
        unique=False,
    )
    op.create_index(
        "ix_work_logs_equipment_id",
        "work_logs",
        ["equipment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_logs_equipment_id", table_name="work_logs")
    op.drop_index(
        "ix_work_logs_source_notification_id",
        table_name="work_logs",
    )
    op.drop_constraint(
        "fk_work_logs_equipment_id_equipment_masters",
        "work_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_work_logs_source_notification_id_notifications",
        "work_logs",
        type_="foreignkey",
    )
    op.drop_column("work_logs", "alarm_code")
    op.drop_column("work_logs", "equipment_id")
    op.drop_column("work_logs", "source_notification_id")

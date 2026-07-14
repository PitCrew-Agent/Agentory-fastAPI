"""작업 로그 계획/완료 구조화 (NEW_LOOP01_WORKLOG01)

content를 plan(작업 계획)으로 rename, completion(작업 완료 내용)·completed_at(완료 시각) 추가
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_worklog_plan_completion"
down_revision: str | Sequence[str] | None = "0020_equipment_alarms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("work_logs", "content", new_column_name="plan")
    op.add_column("work_logs", sa.Column("completion", sa.Text(), nullable=True))
    op.add_column(
        "work_logs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("work_logs", "completed_at")
    op.drop_column("work_logs", "completion")
    op.alter_column("work_logs", "plan", new_column_name="content")

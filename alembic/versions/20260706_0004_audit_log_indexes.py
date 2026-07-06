"""audit log query indexes

Revision ID: 0004_audit_log_indexes
Revises: 0003_auth_sso_audit
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_audit_log_indexes"
down_revision: str | None = "0003_auth_sso_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_audit_logs_action_time", "audit_logs", ["action", "created_at"])
    op.create_index("ix_audit_logs_status_time", "audit_logs", ["status_code", "created_at"])
    op.create_index("ix_audit_logs_success_time", "audit_logs", ["success", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_success_time", table_name="audit_logs")
    op.drop_index("ix_audit_logs_status_time", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_time", table_name="audit_logs")

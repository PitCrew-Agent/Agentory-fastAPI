"""chat_session soft delete 컬럼·부분 인덱스 추가 (BE_CHAT03_DELETE01)

Revision ID: 0013_chat_session_soft_delete
Revises: 0012_notification_hourly_bucket
Create Date: 2026-07-10

화면에서 대화 히스토리를 삭제할 수 있도록 soft delete(deleted_at) 도입
목록·상세는 deleted_at IS NULL만 노출, 히스토리 조회 인덱스를 미삭제 부분 인덱스로 교체
기존 행은 NULL 허용이라 무중단 추가 가능
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_chat_session_soft_delete"
down_revision: str | Sequence[str] | None = "0012_notification_hourly_bucket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_session",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 히스토리 조회 인덱스를 미삭제 부분 인덱스로 교체
    op.drop_index("ix_chat_session_user_created", table_name="chat_session")
    op.create_index(
        "ix_chat_session_user_created",
        "chat_session",
        ["user_sub", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_session_user_created", table_name="chat_session")
    op.create_index(
        "ix_chat_session_user_created",
        "chat_session",
        ["user_sub", "created_at"],
    )
    op.drop_column("chat_session", "deleted_at")

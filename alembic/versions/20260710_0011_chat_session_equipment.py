"""chat_session 설비 컨텍스트·히스토리 인덱스 추가 (BE_CHAT03_HISTORY01)

Revision ID: 0011_chat_session_equipment
Revises: 0010_equipment_manager_user
Create Date: 2026-07-10

대화 히스토리 목록에 장비 표시를 위해 세션에 컨텍스트 설비를 저장
사용자별 최신순 목록 조회 대응 인덱스 추가, 레거시 세션은 설비 NULL 허용
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_chat_session_equipment"
down_revision: str | None = "0010_equipment_manager_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_session", sa.Column("equipment_id", sa.String(50), nullable=True))
    op.create_index("ix_chat_session_user_created", "chat_session", ["user_sub", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_session_user_created", table_name="chat_session")
    op.drop_column("chat_session", "equipment_id")

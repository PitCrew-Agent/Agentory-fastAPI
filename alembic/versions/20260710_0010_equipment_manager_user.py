"""equipment_masters 책임자 유저 FK 추가 (BE_ADMIN01_MANAGER01)

Revision ID: 0010_equipment_manager_user
Revises: 0009_lines
Create Date: 2026-07-10

설비 책임자를 문자열(manager_name) 대신 유저로 지정, 관리자만 배정
유저 삭제 시 책임자 해제(SET NULL), 기존 manager_name/dept는 레거시 폴백으로 유지
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_equipment_manager_user"
down_revision: str | None = "0009_lines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "equipment_masters",
        sa.Column("manager_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_equipment_masters_manager_user",
        "equipment_masters",
        "users",
        ["manager_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_equipment_masters_manager_user", "equipment_masters", ["manager_user_id"])


def downgrade() -> None:
    op.drop_index("ix_equipment_masters_manager_user", table_name="equipment_masters")
    op.drop_constraint("fk_equipment_masters_manager_user", "equipment_masters", type_="foreignkey")
    op.drop_column("equipment_masters", "manager_user_id")

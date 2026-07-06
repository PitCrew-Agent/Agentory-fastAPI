"""equipment_master 책임자·점검일 컬럼 추가 (DEV_DATABASE)

Revision ID: 0002_equipment_meta
Revises: 0001_initial
Create Date: 2026-07-06

대시보드 상세 표기용 manager_name·last_inspection_at 추가 (ERD v2)
기존 행은 NULL 허용이라 무중단 추가 가능
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_equipment_meta"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("equipment_master", sa.Column("manager_name", sa.String(50)))
    op.add_column("equipment_master", sa.Column("last_inspection_at", sa.Date))


def downgrade() -> None:
    op.drop_column("equipment_master", "last_inspection_at")
    op.drop_column("equipment_master", "manager_name")

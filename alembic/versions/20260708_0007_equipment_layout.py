"""equipment_masters 3D 배치 컬럼 추가 (NEW_TWIN01_SCENE01)

Revision ID: 0007_equipment_layout
Revises: 0006_work_logs
Create Date: 2026-07-08

3D 트윈 뷰가 라인·설비 배치를 그대로 재현하도록 설비별 배치값을 마스터에 저장
기존 행은 NULL 허용이라 무중단 추가 가능, 배치값은 시드로 채움
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_equipment_layout"
down_revision: str | None = "0006_work_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("equipment_masters", sa.Column("display_order", sa.Integer))
    op.add_column("equipment_masters", sa.Column("shape", sa.String(30)))
    op.add_column("equipment_masters", sa.Column("bay_zone", sa.String(10)))
    op.add_column("equipment_masters", sa.Column("position_x", sa.Numeric(6, 3)))
    op.add_column("equipment_masters", sa.Column("position_y", sa.Numeric(6, 3)))
    op.add_column("equipment_masters", sa.Column("position_z", sa.Numeric(6, 3)))
    op.add_column("equipment_masters", sa.Column("rotation_y", sa.Numeric(17, 15)))


def downgrade() -> None:
    op.drop_column("equipment_masters", "rotation_y")
    op.drop_column("equipment_masters", "position_z")
    op.drop_column("equipment_masters", "position_y")
    op.drop_column("equipment_masters", "position_x")
    op.drop_column("equipment_masters", "bay_zone")
    op.drop_column("equipment_masters", "shape")
    op.drop_column("equipment_masters", "display_order")

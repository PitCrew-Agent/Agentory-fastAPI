"""equipment_masters 알람 래치 해제 시각 컬럼 추가 (NEW_LOOP01_LATCH01)

Revision ID: 0008_alarm_latch
Revises: 0007_equipment_layout
Create Date: 2026-07-09

설비 상태가 한 번 확정 알람에 걸리면 점검·해제 전까지 유지되도록 래치 기준 시각을 저장
NULL은 해제 이력 없음(전체 이력의 확정 알람 반영), 현장 점검·수리 시 해당 시각으로 갱신
기존 행은 NULL 허용이라 무중단 추가 가능
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_alarm_latch"
down_revision: str | None = "0007_equipment_layout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "equipment_masters",
        sa.Column("alarm_cleared_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("equipment_masters", "alarm_cleared_at")

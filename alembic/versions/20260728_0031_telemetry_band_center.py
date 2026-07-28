"""telemetry band center

Revision ID: 0031_telemetry_band_center
Revises: 0030_knowledge_embedding_768
Create Date: 2026-07-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031_telemetry_band_center"
down_revision: str | None = "0030_knowledge_embedding_768"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 동적 밴드 중심선 컬럼 추가 (BE_SIM01_GEN01), 기존 행은 NULL 허용
    op.add_column(
        "equipment_telemetries", sa.Column("temperature_center", sa.Numeric(5, 2), nullable=True)
    )
    op.add_column(
        "equipment_telemetries", sa.Column("pressure_center", sa.Numeric(5, 2), nullable=True)
    )
    op.add_column(
        "equipment_telemetries", sa.Column("rf_power_center", sa.Numeric(6, 2), nullable=True)
    )
    op.add_column(
        "equipment_telemetries", sa.Column("gas_flow_center", sa.Numeric(7, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("equipment_telemetries", "gas_flow_center")
    op.drop_column("equipment_telemetries", "rf_power_center")
    op.drop_column("equipment_telemetries", "pressure_center")
    op.drop_column("equipment_telemetries", "temperature_center")

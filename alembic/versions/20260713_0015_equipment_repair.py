"""설비 수리 이력·힐 윈도우 래치 추가 (NEW_REPAIR01_HISTORY01)

관리자·현장 책임자가 설비를 수리하면 이력을 남기고(equipment_repairs), 설비 마스터에
수리 시각(repaired_at)을 래치해 시뮬레이터가 힐 윈도우 동안 정상값을 생성하도록 함
누가·언제·어떤 설비를 수리했는지가 작업 현황 조회와 재정비 에이전트의 원천
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_equipment_repair"
down_revision: str | Sequence[str] | None = "0014_alarm_history_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 힐 윈도우 래치 컬럼, NULL 허용이라 무중단 추가 가능
    op.add_column(
        "equipment_masters",
        sa.Column("repaired_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 수리 이력 테이블
    op.create_table(
        "equipment_repairs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("equipment_id", sa.String(length=50), nullable=False),
        sa.Column("repaired_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "repaired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("alarm_code_before", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment_masters.equipment_id"]),
        sa.ForeignKeyConstraint(["repaired_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_equipment_repairs_equip_time",
        "equipment_repairs",
        ["equipment_id", "repaired_at"],
    )
    op.create_index(
        "ix_equipment_repairs_repaired_by",
        "equipment_repairs",
        ["repaired_by"],
    )


def downgrade() -> None:
    op.drop_index("ix_equipment_repairs_repaired_by", table_name="equipment_repairs")
    op.drop_index("ix_equipment_repairs_equip_time", table_name="equipment_repairs")
    op.drop_table("equipment_repairs")
    op.drop_column("equipment_masters", "repaired_at")

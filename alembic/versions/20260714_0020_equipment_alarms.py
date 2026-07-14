"""센서 변수별 알람 이벤트 저널 테이블 추가 (NEW_ALARM01_HISTORY02)

값과 알람을 분리한 실무형 알람 저널, 변수(metric)별 독립 알람의 발생~해제 생명주기 기록
도넛 센서별 집계는 이 테이블 metric 기준 집계로 산출, 대표 alarm_code는 telemetry에 파생 유지
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_equipment_alarms"
down_revision: str | Sequence[str] | None = "0019_knowledge_embedding_notnull"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "equipment_alarms",
        sa.Column("alarm_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.String(50),
            sa.ForeignKey("equipment_masters.equipment_id"),
            nullable=False,
        ),
        sa.Column("metric", sa.String(20), nullable=False),
        sa.Column("alarm_code", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column(
            "raised_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("cleared_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_equipment_alarms_equip_metric_time",
        "equipment_alarms",
        ["equipment_id", "metric", "raised_at"],
    )
    # 활성 알람(cleared_at IS NULL) 부분 인덱스, 발생/해제 전이 판정 조회용
    op.create_index(
        "ix_equipment_alarms_active",
        "equipment_alarms",
        ["equipment_id", "metric"],
        postgresql_where=sa.text("cleared_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_equipment_alarms_active", table_name="equipment_alarms")
    op.drop_index("ix_equipment_alarms_equip_metric_time", table_name="equipment_alarms")
    op.drop_table("equipment_alarms")

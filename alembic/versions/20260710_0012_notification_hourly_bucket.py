"""알림 시간 버킷 중복 억제 컬럼·제약 추가 (NEW_PROACT01_ALERT03)

Revision ID: 0012_notification_hourly_bucket
Revises: 0011_chat_session_equipment
Create Date: 2026-07-10

동일 설비+알람이 반복돼 알림 이력이 난잡해지는 문제를 시간 버킷(정시) 단위로 억제
bucket_hour(발생 시각의 정시 절단)를 추가하고 (equipment_id, alarm_code, bucket_hour)
유니크로 버킷당 1건만 허용, 기존 중복은 버킷별 가장 이른 1건만 남기고 정리
기존 source_log_id 유니크는 버킷 유니크로 대체
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_notification_hourly_bucket"
down_revision: str | Sequence[str] | None = "0011_chat_session_equipment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 컬럼 추가(우선 NULL 허용 후 백필)
    op.add_column(
        "notifications",
        sa.Column("bucket_hour", sa.DateTime(timezone=True), nullable=True),
    )
    # 2) 기존 행 백필: 발생 시각의 정시 절단
    op.execute(sa.text("UPDATE notifications SET bucket_hour = date_trunc('hour', occurred_at)"))
    # 3) 기존 중복 정리: 버킷별 가장 이른 1건만 남기고 삭제
    op.execute(
        sa.text(
            """
            DELETE FROM notifications n
            USING (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY equipment_id, alarm_code, bucket_hour
                           ORDER BY occurred_at, id
                       ) AS rn
                FROM notifications
            ) d
            WHERE n.id = d.id AND d.rn > 1
            """
        )
    )
    # 4) NOT NULL 확정
    op.alter_column("notifications", "bucket_hour", nullable=False)
    # 5) 유니크 제약 교체(source_log_id -> 버킷)
    op.drop_constraint("uq_notifications_source_log", "notifications", type_="unique")
    op.create_unique_constraint(
        "uq_notifications_equip_alarm_bucket",
        "notifications",
        ["equipment_id", "alarm_code", "bucket_hour"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_notifications_equip_alarm_bucket", "notifications", type_="unique")
    op.create_unique_constraint("uq_notifications_source_log", "notifications", ["source_log_id"])
    op.drop_column("notifications", "bucket_hour")

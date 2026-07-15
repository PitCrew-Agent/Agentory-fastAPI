"""알림 중복 억제를 변수+30분 버킷 단위로 변경 (NEW_PROACT01_ALERT03)

Revision ID: 0022_notification_metric_bucket
Revises: 0021_worklog_plan_completion
Create Date: 2026-07-15

동일 설비+알람을 정시(1시간) 버킷당 1건으로 억제하던 규칙을 정식 스펙에 맞춰
동일 설비+변수(metric)+알람을 30분 버킷(00분·30분 경계)당 1건으로 변경
metric 컬럼을 추가하고 bucket_hour를 bucket_start(30분 절단)로, source_log_id를
source_alarm_id(EquipmentAlarm 참조)로 교체, 유니크 제약을 4개 키로 재구성
기존 알림은 telemetry 기반 레거시라 metric NULL·source NULL로 남기고 버킷만 재계산
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_notification_metric_bucket"
down_revision: str | Sequence[str] | None = "0021_worklog_plan_completion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUCKET_SQL = "date_bin(interval '30 minutes', occurred_at, timestamptz '2000-01-01 00:00:00+00')"


def upgrade() -> None:
    # 1) 기존 유니크 제약 해제(정시 버킷 기준)
    op.drop_constraint("uq_notifications_equip_alarm_bucket", "notifications", type_="unique")
    # 2) 변수 컬럼 추가, 레거시 행은 NULL(변수 특정 불가)
    op.add_column("notifications", sa.Column("metric", sa.String(length=20), nullable=True))
    # 3) 버킷 컬럼 리네임(정시 -> 30분), 값은 발생 시각 기준 30분 절단으로 재계산
    op.alter_column("notifications", "bucket_hour", new_column_name="bucket_start")
    op.execute(sa.text(f"UPDATE notifications SET bucket_start = {_BUCKET_SQL}"))
    # 4) 소스 참조 리네임(telemetry log_id -> alarm_id), 레거시 telemetry 참조는 무효라 NULL화
    op.alter_column("notifications", "source_log_id", new_column_name="source_alarm_id")
    op.execute(sa.text("UPDATE notifications SET source_alarm_id = NULL"))
    # 5) 신규 유니크 제약(설비+변수+알람+30분 버킷)
    op.create_unique_constraint(
        "uq_notifications_equip_metric_alarm_bucket",
        "notifications",
        ["equipment_id", "metric", "alarm_code", "bucket_start"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_notifications_equip_metric_alarm_bucket", "notifications", type_="unique"
    )
    op.alter_column("notifications", "source_alarm_id", new_column_name="source_log_id")
    op.alter_column("notifications", "bucket_start", new_column_name="bucket_hour")
    op.execute(sa.text("UPDATE notifications SET bucket_hour = date_trunc('hour', occurred_at)"))
    op.drop_column("notifications", "metric")
    op.create_unique_constraint(
        "uq_notifications_equip_alarm_bucket",
        "notifications",
        ["equipment_id", "alarm_code", "bucket_hour"],
    )

"""장비별 알람 이력 조회 부분 인덱스 추가 (NEW_ALARM01_HISTORY01)

Revision ID: 0014_alarm_history_index
Revises: 0013_chat_session_soft_delete
Create Date: 2026-07-13

장비별 알람 타임라인·코드필터·집계 요약 조회를 위한 부분 인덱스 추가
알람은 전체 텔레메트리의 1% 미만이라 alarm_code 있는 행만 인덱싱해 크기를 최소화
기존 (equipment_id, timestamp) 인덱스는 alarm_code 필터·집계에서 풀 설비 스캔으로 저하
2M행 벤치 기준 코드필터·요약이 약 40ms → sub-ms로 개선, 인덱스 크기는 1/160 (scripts/bench)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_alarm_history_index"
down_revision: str | Sequence[str] | None = "0013_chat_session_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 알람 있는 행만 담는 부분 인덱스, 무중단 추가 가능
    op.create_index(
        "ix_telemetry_equip_alarm_time",
        "equipment_telemetries",
        ["equipment_id", "timestamp"],
        postgresql_where=sa.text("alarm_code IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_telemetry_equip_alarm_time", table_name="equipment_telemetries")

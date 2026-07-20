"""알림에 소속 라인 컬럼 추가 (BE_NOTI01_SCOPE01)

Revision ID: 0027_notification_line_name
Revises: 0026_anomaly_calibration
Create Date: 2026-07-20

담당 라인별 알림 스코핑을 서버에서 수행하기 위해 notifications에 line_name을 추가합니다.
매 조회마다 equipment_masters·lines·user_lines를 3중 조인하는 대신, 알림 적재 시점에
설비 마스터의 line_name을 한 번 해석해 비정규화 보관합니다.
기존 행은 설비 마스터 기준으로 백필하며, 마스터에 없는 설비는 NULL로 남습니다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_notification_line_name"
down_revision: str | Sequence[str] | None = "0026_anomaly_calibration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 라인 컬럼 추가, 설비 마스터 미등록 설비는 NULL 허용
    op.add_column("notifications", sa.Column("line_name", sa.String(length=50), nullable=True))
    # 2) 기존 알림 백필, 설비 마스터의 line_name을 그대로 사용
    op.execute(
        """
        UPDATE notifications n
        SET line_name = e.line_name
        FROM equipment_masters e
        WHERE e.equipment_id = n.equipment_id
        """
    )
    # 3) 담당 라인 스코핑 조회용 복합 인덱스, 이력 정렬키(occurred_at)와 결합
    op.create_index(
        "ix_notifications_line_occurred_at", "notifications", ["line_name", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_line_occurred_at", table_name="notifications")
    op.drop_column("notifications", "line_name")

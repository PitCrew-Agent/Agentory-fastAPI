"""알림 읽음 상태를 사용자별로 분리 (BE_NOTI01_SCOPE01)

Revision ID: 0028_notification_read_state
Revises: 0027_notification_line_name
Create Date: 2026-07-20

읽음 여부를 notifications.is_read 단일 플래그로 보관하던 구조에서는 한 사용자가 읽으면
모든 사용자에게 읽음으로 보였습니다. 읽음은 사용자마다 다른 상태이므로 (알림, 사용자)
연결 테이블로 분리합니다.

기존에 읽음 처리된 알림은 현재 화면 상태를 유지하기 위해 전체 사용자 기준으로 이관하며,
이관 후 is_read 컬럼을 제거합니다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_notification_read_state"
down_revision: str | Sequence[str] | None = "0027_notification_line_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 사용자별 읽음 상태 테이블, 알림·유저 삭제 시 연쇄 정리
    op.create_table(
        "notification_reads",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notification_id", "user_id", name="uq_notification_reads_notification_user"
        ),
    )
    op.create_index(
        "ix_notification_reads_user_notification",
        "notification_reads",
        ["user_id", "notification_id"],
    )
    # 2) 기존 읽음 상태 이관, 전역 플래그였으므로 전체 사용자에게 동일 적용
    op.execute(
        """
        INSERT INTO notification_reads (notification_id, user_id)
        SELECT n.id, u.id
        FROM notifications n
        CROSS JOIN users u
        WHERE n.is_read = true
        """
    )
    # 3) 전역 플래그 제거, 이후 읽음 판정은 notification_reads 존재 여부로 수행
    op.drop_index("ix_notifications_is_read", table_name="notifications")
    op.drop_column("notifications", "is_read")


def downgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("is_read", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
    # 사용자 1명이라도 읽었으면 읽음으로 되돌림 (전역 플래그 의미상 손실 발생)
    op.execute(
        """
        UPDATE notifications n
        SET is_read = true
        WHERE EXISTS (SELECT 1 FROM notification_reads r WHERE r.notification_id = n.id)
        """
    )
    op.drop_index("ix_notification_reads_user_notification", table_name="notification_reads")
    op.drop_table("notification_reads")

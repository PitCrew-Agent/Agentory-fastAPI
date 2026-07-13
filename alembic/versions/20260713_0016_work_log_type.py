"""작업 로그 유형 컬럼 추가 (NEW_LOOP01_WORKLOG02)

작업 로그 작성 시 유형(정기점검/수리점검/예방점검/긴급수리/기타)을 단일 선택하도록 컬럼 추가
기존 행은 유형이 없으므로 '기타'로 백필한 뒤 NOT NULL·체크 제약을 적용해 무중단 전환
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_work_log_type"
down_revision: str | Sequence[str] | None = "0015_equipment_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLOWED = "('정기점검', '수리점검', '예방점검', '긴급수리', '기타')"


def upgrade() -> None:
    # 우선 nullable로 추가 후 기존 행을 '기타'로 백필
    op.add_column("work_logs", sa.Column("work_type", sa.String(length=20), nullable=True))
    op.execute("UPDATE work_logs SET work_type = '기타' WHERE work_type IS NULL")
    # 백필 완료 후 NOT NULL·값 제약 적용
    op.alter_column("work_logs", "work_type", nullable=False)
    op.create_check_constraint("ck_work_logs_work_type", "work_logs", f"work_type IN {_ALLOWED}")


def downgrade() -> None:
    op.drop_constraint("ck_work_logs_work_type", "work_logs", type_="check")
    op.drop_column("work_logs", "work_type")

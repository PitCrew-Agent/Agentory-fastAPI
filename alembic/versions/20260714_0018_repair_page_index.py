"""수리 이력 전역 페이지 인덱스 (NEW_REPAIR01_HISTORY01)

설비 미지정 전역 수리 이력 페이지는 repaired_at 역순 정렬이나 지원 인덱스가 없어 순차 스캔
(repaired_at, id) 복합 인덱스를 추가해 row-value 키셋 커서가 커서 위치로 직접 seek 하도록 개선
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_repair_page_index"
down_revision: str | Sequence[str] | None = "0017_incident_work_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_equipment_repairs_repaired_at_id",
        "equipment_repairs",
        ["repaired_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_equipment_repairs_repaired_at_id", table_name="equipment_repairs")

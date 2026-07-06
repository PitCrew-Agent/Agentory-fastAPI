"""telemetry 도메인 테이블명 복수화 (DEV_DATABASE)

Revision ID: 0003_pluralize_telemetry
Revises: 0002_equipment_meta
Create Date: 2026-07-06

auth 팀 컨벤션 정렬로 telemetry 도메인 테이블명 복수화
  equipment_master → equipment_masters
  equipment_telemetry → equipment_telemetries
FK·인덱스·제약은 rename_table로 자동 승계되므로 데이터 손실 없이 이름만 변경
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_pluralize_telemetry"
down_revision: str | None = "0002_equipment_meta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("equipment_master", "equipment_masters")
    op.rename_table("equipment_telemetry", "equipment_telemetries")


def downgrade() -> None:
    op.rename_table("equipment_telemetries", "equipment_telemetry")
    op.rename_table("equipment_masters", "equipment_master")

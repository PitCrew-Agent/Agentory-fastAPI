"""라인명 영문 표기를 한국어로 통일 (BE_MCP02_TELEMETRY01)

Revision ID: 0023_line_name_korean
Revises: 0022_notification_metric_bucket
Create Date: 2026-07-15

라인명 표기를 한국어로 통일하면서 코드·시드는 갱신됐으나 기존 DB 행은 영문("A-Line")으로
남아, 라인 기준 조회(line_name 정확 일치)가 빈 결과를 냅니다. 기존 데이터의 "-Line" 접미사를
"라인"으로 치환해 equipment_masters.line_name과 lines.code를 정합시킵니다. user_lines는
lines.id로 연결되어 영향받지 않습니다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_line_name_korean"
down_revision: str | Sequence[str] | None = "0022_notification_metric_bucket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 기존 영문 라인명("X-Line")을 한국어("X라인")로 치환, 이미 한국어인 행은 미해당
    op.execute(
        sa.text(
            "UPDATE equipment_masters SET line_name = replace(line_name, '-Line', '라인') "
            "WHERE line_name LIKE '%-Line'"
        )
    )
    op.execute(
        sa.text("UPDATE lines SET code = replace(code, '-Line', '라인') WHERE code LIKE '%-Line'")
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE equipment_masters SET line_name = replace(line_name, '라인', '-Line') "
            "WHERE line_name LIKE '%라인'"
        )
    )
    op.execute(
        sa.text("UPDATE lines SET code = replace(code, '라인', '-Line') WHERE code LIKE '%라인'")
    )

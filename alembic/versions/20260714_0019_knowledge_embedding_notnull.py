"""knowledge_collection embedding NOT NULL 정합 (BE_MCP04_RAG01)

초기 스키마에서 embedding 컬럼에 nullable=False 누락돼 모델(NOT NULL)과 DB(nullable) 드리프트 발생
벡터 검색 전용 컬럼이라 embedding NULL 청크는 무의미하므로 모델 기준 NOT NULL로 정합
적용 전 embedding NULL 0건 확인 완료, 백필 불필요
"""

from collections.abc import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0019_knowledge_embedding_notnull"
down_revision: str | Sequence[str] | None = "0018_repair_page_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.alter_column(
        "knowledge_collection",
        "embedding",
        existing_type=Vector(EMBEDDING_DIM),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "knowledge_collection",
        "embedding",
        existing_type=Vector(EMBEDDING_DIM),
        nullable=True,
    )

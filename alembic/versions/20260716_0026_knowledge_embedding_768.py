"""knowledge_collection 임베딩 차원 768 전환 (AI_RAG01_CHUNK01)"""

from collections.abc import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0026_knowledge_embedding_768"
down_revision: str | Sequence[str] | None = "0025_anomaly_shadow_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_DIM = 768
OLD_DIM = 1536


def _swap_embedding_dim(from_dim: int, to_dim: int) -> None:
    # 차원 변경은 기존 벡터와 비호환, 인덱스 드롭 → 무효 데이터 제거 → 차원 변경 → 인덱스 재생성
    op.drop_index("ix_knowledge_embedding_hnsw", table_name="knowledge_collection")
    op.execute("TRUNCATE TABLE knowledge_collection")
    op.alter_column(
        "knowledge_collection",
        "embedding",
        existing_type=Vector(from_dim),
        type_=Vector(to_dim),
        existing_nullable=False,
    )
    op.create_index(
        "ix_knowledge_embedding_hnsw",
        "knowledge_collection",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def upgrade() -> None:
    _swap_embedding_dim(OLD_DIM, NEW_DIM)


def downgrade() -> None:
    _swap_embedding_dim(NEW_DIM, OLD_DIM)

"""knowledge_collection 벡터 차원 1536 → 1024 (AI_RAG01_CHUNK01)

임베더를 KURE-v1 dense(1024차원)로 교체하면서 벡터 컬럼 차원 변경
1536차원 벡터는 1024차원으로 변환 불가라 기존 청크를 비우고 재적재 전제
HNSW 인덱스는 컬럼 차원에 묶여 있어 삭제 후 재생성
청킹도 문자 800/100에서 docling HybridChunker(max_tokens=1024)로 바뀌어 어차피 전량 재적재 대상
재적재: uv run python scripts/ingest_manuals.py
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_embedding_dim_1024"
down_revision: str | Sequence[str] | None = "0021_worklog_plan_completion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HNSW_INDEX = "ix_knowledge_embedding_hnsw"
_TABLE = "knowledge_collection"


def _switch_dim(dim: int) -> None:
    """벡터 컬럼 차원 교체, 인덱스 제거 → 청크 비우기 → 차원 변경 → 인덱스 재생성"""
    op.drop_index(_HNSW_INDEX, table_name=_TABLE)
    op.execute(f"DELETE FROM {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN embedding TYPE vector({dim})")
    op.create_index(
        _HNSW_INDEX,
        _TABLE,
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def upgrade() -> None:
    _switch_dim(1024)


def downgrade() -> None:
    _switch_dim(1536)

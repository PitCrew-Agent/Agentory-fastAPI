"""벡터 스토어 포트, 교체 가능 지점 ③ (비기능: 확장성)

기본 구현은 store/pgvector.py의 PgVectorStore, 필요 시 Chroma/FAISS 어댑터로 교체 가능
"""

from typing import Any, Protocol


class VectorStore(Protocol):
    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 3,
        equipment_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """코사인 유사도 Top-K 검색, 반환: [{doc_id, content, score}]"""
        ...

    async def upsert(self, chunks: list[dict[str, Any]]) -> int:
        """청크 적재, 반환: 적재 건수"""
        ...

    async def fetch_all(self) -> list[dict[str, Any]]:
        """전체 청크 조회, 반환: [{chunk_id, doc_id, content}]

        어휘 색인 구축용, 벡터는 제외해 전송량 절감 (AI_RAG02_HYBRID01)
        """
        ...

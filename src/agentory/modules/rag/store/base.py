"""벡터 스토어 포트, 교체 가능 지점 ③ (비기능: 확장성)

TODO(김건): pgvector 기본 구현 추가, 필요 시 Chroma/FAISS 어댑터로 교체 가능
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

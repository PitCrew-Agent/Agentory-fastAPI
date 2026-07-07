"""pgvector 벡터 스토어, 교체 가능 지점 ③ 구현 (AI_RAG01_CHUNK01)

knowledge_collection에 청크 적재·코사인 유사도 검색
session_factory 주입으로 테스트는 자체 엔진 세션 사용 가능
"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentory.core.db import SessionLocal
from agentory.modules.rag.store.models import KnowledgeChunk


class PgVectorStore:
    """VectorStore 포트의 pgvector 구현"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
        self._session_factory = session_factory

    async def upsert(self, chunks: list[dict[str, Any]]) -> int:
        """청크 적재, 반환: 적재 건수

        doc_id별 기존 행 삭제 후 삽입으로 재적재 멱등성 확보
        """
        if not chunks:
            return 0
        doc_ids = {chunk["doc_id"] for chunk in chunks}
        async with self._session_factory() as session:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(doc_ids)))
            session.add_all([KnowledgeChunk(**chunk) for chunk in chunks])
            await session.commit()
        return len(chunks)

    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 3,
        equipment_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """코사인 유사도 Top-K 검색, 반환: [{doc_id, content, score}]"""
        distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
        stmt = select(KnowledgeChunk, distance.label("distance"))
        if equipment_type is not None:
            stmt = stmt.where(KnowledgeChunk.equipment_type == equipment_type)
        stmt = stmt.order_by(distance).limit(top_k)
        async with self._session_factory() as session:
            rows = await session.execute(stmt)
            return [
                {"doc_id": chunk.doc_id, "content": chunk.content, "score": 1.0 - distance_value}
                for chunk, distance_value in rows
            ]

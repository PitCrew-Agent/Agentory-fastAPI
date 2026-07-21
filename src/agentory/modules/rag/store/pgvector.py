"""pgvector 벡터 스토어, 교체 가능 지점 ③ 구현 (AI_RAG01_CHUNK01)

knowledge_collection에 청크 적재·코사인 유사도 검색
session_factory 주입으로 테스트는 자체 엔진 세션 사용 가능
"""

from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentory.core.config import get_settings
from agentory.core.db import SessionLocal
from agentory.modules.rag.store.models import KnowledgeChunk


class PgVectorStore:
    """VectorStore 포트의 pgvector 구현"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
        self._session_factory = session_factory

    async def embedding_column_dim(self) -> int | None:
        """knowledge_collection.embedding 컬럼의 pgvector 차원, 부재·미지정이면 None"""
        async with self._session_factory() as session:
            row = await session.execute(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = to_regclass('knowledge_collection') "
                    "AND attname = 'embedding' AND NOT attisdropped"
                )
            )
            dim = row.scalar_one_or_none()
        return dim if dim and dim > 0 else None

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

    async def fetch_all(self) -> list[dict[str, Any]]:
        """전체 청크 조회, 반환: [{chunk_id, doc_id, content}]

        어휘 색인 구축용이라 embedding 컬럼은 조회하지 않음 (AI_RAG02_HYBRID01)
        """
        stmt = select(
            KnowledgeChunk.chunk_id, KnowledgeChunk.doc_id, KnowledgeChunk.content
        ).order_by(KnowledgeChunk.chunk_id)
        async with self._session_factory() as session:
            rows = await session.execute(stmt)
            return [
                {"chunk_id": chunk_id, "doc_id": doc_id, "content": content}
                for chunk_id, doc_id, content in rows
            ]

    async def purge_except(self, keep_doc_ids: set[str]) -> int:
        """매니페스트에 없는 doc_id 청크 제거, 반환: 삭제 건수

        코퍼스에서 빠진 구 문서(교체 전 doc_id 등)의 잔존 청크 정리
        keep_doc_ids가 비면 전체 삭제 위험이 있어 아무것도 지우지 않음
        """
        if not keep_doc_ids:
            return 0
        async with self._session_factory() as session:
            result = await session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.notin_(keep_doc_ids))
            )
            await session.commit()
        return result.rowcount or 0

    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 3,
        equipment_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """코사인 유사도 Top-K 검색, 반환: [{doc_id, content, score}]

        equipment_type 필터가 결과 0건이면 미필터로 폴백해 잘못된 태그로 검색이 죽는 것을 방지
        (적재 메타 태그와 워커가 넘긴 값이 불일치해도 유사도 근거를 놓치지 않게 함)
        """
        results = await self._search(query_embedding, top_k=top_k, equipment_type=equipment_type)
        if not results and equipment_type is not None:
            results = await self._search(query_embedding, top_k=top_k, equipment_type=None)
        return results

    async def _search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        equipment_type: str | None,
    ) -> list[dict[str, Any]]:
        # 단일 벡터 검색(필터 적용/미적용 공용), 폴백 로직은 search가 담당
        distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
        stmt = select(KnowledgeChunk, distance.label("distance"))
        if equipment_type is not None:
            stmt = stmt.where(KnowledgeChunk.equipment_type == equipment_type)
        stmt = stmt.order_by(distance).limit(top_k)
        async with self._session_factory() as session:
            rows = await session.execute(stmt)
            # chunk_id는 어휘 후보와 융합할 때 청크 식별 키로 사용 (AI_RAG02_HYBRID01)
            return [
                {
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "content": chunk.content,
                    "score": 1.0 - distance_value,
                }
                for chunk, distance_value in rows
            ]


async def verify_embedding_dim() -> None:
    """DB 벡터 컬럼 차원과 설정 embedding_dim 대조, 불일치면 RuntimeError로 기동 차단 (B)

    컬럼 부재(마이그레이션 전)는 통과, 마이그레이션이 차원을 강제하므로 실제 값 불일치만 검사
    """
    db_dim = await PgVectorStore().embedding_column_dim()
    expected = get_settings().embedding_dim
    if db_dim is not None and db_dim != expected:
        raise RuntimeError(
            f"pgvector 컬럼 차원({db_dim})과 embedding_dim({expected}) 불일치, "
            "마이그레이션·재적재 필요"
        )

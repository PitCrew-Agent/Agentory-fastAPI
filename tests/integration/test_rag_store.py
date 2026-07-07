"""pgvector 스토어 통합 테스트 (AI_RAG01_CHUNK01)

실제 PostgreSQL(pgvector) 필요, docker compose up db 후 실행
DB 미기동 시 skip, 센티넬 doc_id로 실데이터와 격리·정리
"""

import hashlib
import random

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.rag.store.models import EMBEDDING_DIM, KnowledgeChunk
from agentory.modules.rag.store.pgvector import PgVectorStore

DOC_ID = "MAN-TST-901"  # 센티넬, 실데이터 충돌 방지
EQUIP = "TestEtching"


class FakeEmbedder:
    """결정론적 해시 기반 임베더, 동일 텍스트는 동일 벡터"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(value) for value in texts]

    @staticmethod
    def _vector(value: str) -> list[float]:
        seed = int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]


async def _chunks_for(
    embedder: FakeEmbedder, contents: list[str], *, equipment_type: str | None = EQUIP
) -> list[dict]:
    chunks = [
        {
            "doc_id": DOC_ID,
            "chunk_index": index,
            "equipment_type": equipment_type,
            "alarm_code": "ERR-999",
            "content": content,
        }
        for index, content in enumerate(contents)
    ]
    embeddings = await embedder.embed(contents)
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        chunk["embedding"] = embedding
    return chunks


@pytest.fixture
async def maker():
    # 테스트별 자체 엔진(NullPool), 전역 엔진의 이벤트 루프 바인딩 문제 회피
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async def cleanup() -> None:
        async with session_maker() as session:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == DOC_ID))
            await session.commit()

    await cleanup()
    yield session_maker
    await cleanup()
    await engine.dispose()


async def _count(session_maker: async_sessionmaker) -> int:
    async with session_maker() as session:
        stmt = (
            select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.doc_id == DOC_ID)
        )
        return await session.scalar(stmt)


async def test_upsert_returns_count_and_search_finds_top1(maker):
    embedder = FakeEmbedder()
    store = PgVectorStore(session_factory=maker)
    contents = ["에칭 설비 온도 상승 대응 절차", "압력 저하 점검 절차", "가스 유량 이상 조치"]
    chunks = await _chunks_for(embedder, contents)

    count = await store.upsert(chunks)
    assert count == 3

    query_vec = (await embedder.embed([contents[0]]))[0]
    results = await store.search(query_vec, top_k=3)
    assert results[0]["doc_id"] == DOC_ID
    assert results[0]["content"] == contents[0]
    assert 0.0 <= results[0]["score"] <= 1.0
    assert results[0]["score"] > 0.99  # 동일 벡터라 코사인 거리 ~0


async def test_search_respects_equipment_type_filter(maker):
    embedder = FakeEmbedder()
    store = PgVectorStore(session_factory=maker)
    await store.upsert(await _chunks_for(embedder, ["에칭 절차"], equipment_type=EQUIP))
    query_vec = (await embedder.embed(["에칭 절차"]))[0]

    included = await store.search(query_vec, top_k=5, equipment_type=EQUIP)
    assert any(row["doc_id"] == DOC_ID for row in included)

    excluded = await store.search(query_vec, top_k=5, equipment_type="NoSuchType")
    assert all(row["doc_id"] != DOC_ID for row in excluded)


async def test_upsert_is_idempotent_by_doc_id(maker):
    embedder = FakeEmbedder()
    store = PgVectorStore(session_factory=maker)
    chunks = await _chunks_for(embedder, ["a", "b", "c"])

    await store.upsert(chunks)
    await store.upsert(chunks)  # 재적재해도 doc_id 행 수 불변

    assert await _count(maker) == 3

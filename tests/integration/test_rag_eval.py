"""RAG 검색 품질 평가 통합 테스트 (AI_RAG02_EVAL01)

실제 PostgreSQL(pgvector) 필요, docker compose up db 후 실행
DB 미기동 시 skip, 센티넬 doc_id로 실데이터와 격리
"""

import hashlib
import random

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.rag.eval import evaluate_retrieval
from agentory.modules.rag.store.models import EMBEDDING_DIM, KnowledgeChunk
from agentory.modules.rag.store.pgvector import PgVectorStore

DOC_ID = "MAN-TST-901"
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


async def _chunks_for(embedder: FakeEmbedder, contents: list[str]) -> list[dict]:
    chunks = [
        {
            "doc_id": DOC_ID,
            "chunk_index": index,
            "equipment_type": EQUIP,
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


async def test_evaluate_retrieval_finds_expected_doc_with_pgvector(maker):
    embedder = FakeEmbedder()
    store = PgVectorStore(session_factory=maker)
    contents = ["에칭 설비 온도 상승 대응 절차", "압력 저하 점검 절차", "가스 유량 이상 조치"]
    await store.upsert(await _chunks_for(embedder, contents))
    cases = [
        {
            "id": "GOLDEN-TST-001",
            "query": contents[0],
            "expect": {"rag_hit_docs": [DOC_ID]},
        }
    ]

    report = await evaluate_retrieval(cases, embedder=embedder, store=store, k=3)

    assert report["n"] == 1
    assert report["hit@k"] == 1.0
    assert report["recall@k"] == 1.0
    assert report["mrr"] == 1.0
    assert report["per_case"][0]["retrieved"][0] == DOC_ID

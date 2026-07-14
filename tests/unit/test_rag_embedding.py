"""임베더 팩토리·KURE 어댑터 단위 테스트 (AI_RAG01_CHUNK01)

실제 모델 로드는 무거우므로 encoder를 주입해 인코딩 경로만 검증
팩토리는 settings 캐시(lru_cache)를 비우고 환경 변수로 provider를 바꿔 검증
"""

import numpy as np
import pytest

from agentory.core.config import Settings, get_settings
from agentory.modules.rag.embedding.factory import SUPPORTED_PROVIDERS, get_embedder
from agentory.modules.rag.embedding.kure import EMBEDDING_DIM, KureEmbedder
from agentory.modules.rag.store.models import EMBEDDING_DIM as COLUMN_DIM


class FakeEncoder:
    """SentenceTransformer 대역, encode 호출 인자를 기록"""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        self.calls: list[dict] = []

    def encode(self, texts, **kwargs):
        self.calls.append({"texts": list(texts), **kwargs})
        return np.ones((len(texts), self.dim), dtype=np.float32)


@pytest.fixture
def clear_settings_cache():
    """settings 캐시 초기화, 환경 변수 변경이 반영되도록 전후로 비움"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_kure_embed_returns_vectors_of_column_dim():
    embedder = KureEmbedder(encoder=FakeEncoder())
    vectors = await embedder.embed(["챔버 압력 이상", "냉각수 온도 상승"])
    assert len(vectors) == 2
    assert all(len(vector) == COLUMN_DIM for vector in vectors)


async def test_kure_embed_requests_normalized_vectors():
    # 코사인 검색 전제라 정규화 벡터를 요구해야 함
    encoder = FakeEncoder()
    await KureEmbedder(encoder=encoder).embed(["점검"])
    assert encoder.calls[0]["normalize_embeddings"] is True


async def test_kure_embed_uses_configured_batch_size():
    encoder = FakeEncoder()
    await KureEmbedder(batch_size=8, encoder=encoder).embed(["점검"])
    assert encoder.calls[0]["batch_size"] == 8


async def test_kure_embed_empty_returns_empty_without_encoding():
    encoder = FakeEncoder()
    assert await KureEmbedder(encoder=encoder).embed([]) == []
    assert encoder.calls == []


def test_kure_dim_matches_vector_column_dim():
    # 어댑터 출력 차원과 knowledge_collection 벡터 컬럼 차원이 어긋나면 적재가 실패
    assert EMBEDDING_DIM == COLUMN_DIM


def test_factory_returns_kure_embedder(monkeypatch, clear_settings_cache):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "kure-v1")
    assert isinstance(get_embedder(), KureEmbedder)


def test_default_provider_is_kure():
    # 팀원이 .env 없이 받아도 KURE 경로로 동작해야 함
    assert Settings.model_fields["embedding_provider"].default == "kure-v1"


def test_factory_rejects_unknown_provider(monkeypatch, clear_settings_cache):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "unknown-model")
    with pytest.raises(ValueError):
        get_embedder()


def test_supported_providers_are_expected(clear_settings_cache):
    assert SUPPORTED_PROVIDERS == ("kure-v1", "openai")

"""multilingual-e5 임베딩 어댑터, 교체 가능 지점 ② 구현 (AI_RAG01_CHUNK01)

로컬 sentence-transformers 기반, 문서=passage·질의=query 비대칭 프리픽스
CPU 인코딩, 코사인 검색 위해 벡터 L2 정규화
모델 로드가 무거워 모델명별 1회 로드 후 프로세스 내 재사용
동기 encode를 asyncio.to_thread로 감싸 이벤트 루프 블로킹 방지
"""

import asyncio
from typing import TYPE_CHECKING

from agentory.core.config import get_settings
from agentory.modules.rag.embedding.base import Embedder

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

# e5 계열 비대칭 프리픽스, 문서와 질의를 다른 표면형으로 인코딩해야 검색 성능 확보
_PASSAGE_PREFIX = "passage: "
_QUERY_PREFIX = "query: "
# multilingual-e5-base(768차원), EMBEDDING_DIM 기본값·knowledge_collection 스키마와 일치
_DEFAULT_MODEL = "intfloat/multilingual-e5-base"

# 모델명별 SentenceTransformer 캐시, 임포트·가중치 로드가 무거워 최초 사용 시 1회 생성
_models: dict[str, "SentenceTransformer"] = {}


def _get_model(name: str) -> "SentenceTransformer":
    """모델명 기준 SentenceTransformer 지연 로드 후 재사용"""
    model = _models.get(name)
    if model is None:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(name, device="cpu")
        _models[name] = model
    return model


class E5Embedder:
    """Embedder 포트의 multilingual-e5 구현"""

    def __init__(self, model: str | None = None) -> None:
        self._model_name = model or get_settings().embedding_model or _DEFAULT_MODEL

    def _encode(self, texts: list[str]) -> list[list[float]]:
        """프리픽스 부착 텍스트를 정규화 벡터로 인코딩 (동기, 스레드에서 호출)"""
        model = _get_model(self._model_name)
        arr = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [row.tolist() for row in arr]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """문서(passage) 목록 임베딩, passage 프리픽스 부착"""
        if not texts:
            return []
        inputs = [_PASSAGE_PREFIX + text for text in texts]
        return await asyncio.to_thread(self._encode, inputs)

    async def embed_query(self, text: str) -> list[float]:
        """질의 임베딩, query 프리픽스 부착"""
        [vector] = await asyncio.to_thread(self._encode, [_QUERY_PREFIX + text])
        return vector


def get_embedder() -> Embedder:
    """E5 임베더 반환, 팩토리 미경유 직접 사용 대비 헬퍼"""
    return E5Embedder()

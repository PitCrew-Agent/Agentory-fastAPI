"""임베딩 어댑터 패키지, provider 설정으로 구현 선택 (교체 가능 지점 ②)

get_embedder가 settings.embedding_provider로 어댑터 디스패치
e5=로컬 sentence-transformers(기본), openai=OpenAI API(롤백·비교용)
"""

from agentory.core.config import get_settings
from agentory.modules.rag.embedding.base import Embedder


def get_embedder() -> Embedder:
    """settings.embedding_provider 기반 임베더 반환"""
    provider = (get_settings().embedding_provider or "e5").lower()
    if provider == "openai":
        from agentory.modules.rag.embedding.openai import OpenAIEmbedder

        return OpenAIEmbedder()
    if provider == "e5":
        from agentory.modules.rag.embedding.e5 import E5Embedder

        return E5Embedder()
    raise ValueError(f"지원하지 않는 embedding_provider: {provider}")

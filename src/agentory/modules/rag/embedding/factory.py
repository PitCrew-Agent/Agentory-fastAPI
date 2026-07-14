"""임베더 팩토리, EMBEDDING_PROVIDER 기반 어댑터 선택 (AI_RAG01_CHUNK01)

provider: kure-v1 | openai
provider마다 벡터 차원이 달라(1024 대 1536) 교체 시 컬럼 마이그레이션과 전체 재적재 필요
어댑터 import는 지연 수행, openai 사용 시 sentence-transformers를 끌어오지 않도록 분리
"""

from agentory.modules.rag.embedding.base import Embedder

SUPPORTED_PROVIDERS = ("kure-v1", "openai")


def get_embedder() -> Embedder:
    """settings.embedding_provider 기반 임베더 반환"""
    from agentory.core.config import get_settings

    provider = get_settings().embedding_provider.strip().lower()
    if provider == "kure-v1":
        from agentory.modules.rag.embedding.kure import KureEmbedder

        return KureEmbedder()
    if provider == "openai":
        from agentory.modules.rag.embedding.openai import OpenAIEmbedder

        return OpenAIEmbedder()
    raise ValueError(
        f"지원하지 않는 EMBEDDING_PROVIDER: {provider}, 가능한 값: {', '.join(SUPPORTED_PROVIDERS)}"
    )

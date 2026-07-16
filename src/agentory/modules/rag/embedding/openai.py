"""OpenAI 임베딩 어댑터, 교체 가능 지점 ② 구현 (AI_RAG01_CHUNK01)

llm/base.py의 챗 모델 팩토리와 동일 패턴, settings 기반 구성
EMBEDDING_MODEL 미설정 시 text-embedding-3-small(1536차원)로 폴백
"""

from langchain_openai import OpenAIEmbeddings

from agentory.core.config import get_settings

# 1536차원, EMBEDDING_DIM 기본값·knowledge_collection 스키마와 일치
_DEFAULT_MODEL = "text-embedding-3-small"


class OpenAIEmbedder:
    """Embedder 포트의 OpenAI 구현"""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self._client = OpenAIEmbeddings(
            model=model or settings.embedding_model or _DEFAULT_MODEL,
            api_key=api_key or settings.openai_api_key,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """문서 목록을 임베딩 벡터 목록으로 변환 (차원: settings.embedding_dim)"""
        return await self._client.aembed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        """질의 임베딩, OpenAI는 대칭 모델이라 문서와 동일 인코딩"""
        return await self._client.aembed_query(text)

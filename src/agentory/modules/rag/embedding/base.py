"""임베딩 포트, 교체 가능 지점 ② (비기능: 확장성)

기본 구현은 embedding/openai.py의 OpenAIEmbedder, 다른 모델 어댑터도 이 패키지에 추가
비대칭 임베더(e5 등) 대응 위해 문서(embed)·질의(embed_query) 인코딩 경로 분리
대칭 임베더(OpenAI 등)는 두 경로가 동일 인코딩
"""

from typing import Protocol


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """문서(passage) 목록을 임베딩 벡터 목록으로 변환 (차원: settings.embedding_dim)"""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """질의(query) 하나를 임베딩 벡터로 변환, 비대칭 임베더는 query 프리픽스 적용"""
        ...

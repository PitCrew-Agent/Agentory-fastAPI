"""임베딩 포트, 교체 가능 지점 ② (비기능: 확장성)

기본 구현은 embedding/kure.py의 KureEmbedder, 다른 모델 어댑터도 이 패키지에 추가
어댑터 선택은 embedding/factory.py의 get_embedder(EMBEDDING_PROVIDER 기반)가 담당
"""

from typing import Protocol


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩 벡터 목록으로 변환 (차원: settings.embedding_dim)"""
        ...

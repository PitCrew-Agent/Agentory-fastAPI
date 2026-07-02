"""임베딩 포트, 교체 가능 지점 ② (비기능: 확장성)

TODO(김건): 구체 모델(OpenAI·Voyage·오픈소스 등) 어댑터를 이 패키지에 추가
"""

from typing import Protocol


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩 벡터 목록으로 변환 (차원: settings.embedding_dim)"""
        ...

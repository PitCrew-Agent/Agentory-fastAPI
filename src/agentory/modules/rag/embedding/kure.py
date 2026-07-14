"""KURE-v1 임베딩 어댑터, 교체 가능 지점 ② 구현 (AI_RAG01_CHUNK01)

질의는 한국어, 코퍼스는 영어인 cross-lingual 조건에서 한국어 질의 표현력이 검색 신뢰도를 좌우
KURE-v1은 BGE-M3를 한국어로 특화 학습한 모델이라 이 조건에 부합, 임베딩 스윕 결과 1위
(golden v4 core 기준 hit@1 0.451, openai text-embedding-3-small 0.396 대비 13.9% 개선)

BGE-M3 계열이라 질의 접두사가 필요 없는 대칭 모델, 질의·문서를 동일 경로로 인코딩
코사인 검색 전제라 항상 정규화 벡터 반환
모델 로드(약 2.2GB)가 무거워 최초 1회 로드 후 인스턴스 단위 재사용, 첫 호출이 로드 시간을
떠안지 않도록 서버 기동 시 warmup 선행
"""

import asyncio
from typing import Any

from agentory.core.config import get_settings

# KURE-v1 dense 출력 차원, knowledge_collection 벡터 컬럼·EMBEDDING_DIM과 일치 필수
EMBEDDING_DIM = 1024


class KureEmbedder:
    """Embedder 포트의 KURE-v1 구현, 로컬 추론 (sentence-transformers)

    encoder 주입 시 모델 로드 없이 동작 (테스트용)
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
        encoder: Any = None,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.kure_model
        # 빈 문자열은 미지정으로 취급, sentence-transformers 기본 선택(가용하면 cuda)에 위임
        self._device = device or settings.kure_device or None
        self._batch_size = batch_size or settings.kure_batch_size
        self._encoder = encoder

    def warmup(self) -> None:
        """모델 선로드, 첫 검색이 로드 시간을 떠안지 않도록 기동 시 호출"""
        self._get_encoder()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩 벡터 목록으로 변환 (차원: settings.embedding_dim)

        로컬 CPU 추론이라 이벤트 루프를 막지 않도록 워커 스레드로 분리
        """
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)

    def _get_encoder(self):
        """SentenceTransformer 지연 로드, 최초 1회만 모델 적재"""
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("sentence-transformers 미설치, uv sync 실행 필요") from exc

            self._encoder = SentenceTransformer(self._model, device=self._device)
        return self._encoder

    def _encode(self, texts: list[str]) -> list[list[float]]:
        """동기 인코딩, 코사인 검색 전제라 정규화 벡터 반환"""
        vectors = self._get_encoder().encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

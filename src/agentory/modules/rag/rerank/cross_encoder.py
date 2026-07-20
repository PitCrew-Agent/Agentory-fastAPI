"""CrossEncoder 기반 리랭커 어댑터, 교체 가능 지점 ③ 구현 (AI_RAG02_RERANK01)

로컬 sentence-transformers CrossEncoder, (query, 문서) 쌍을 관련도 점수로 재정렬
bge-reranker-v2-m3(기본, 고품질)·mmarco-mMiniLMv2(경량)는 모델명만 다름
CPU 추론, 모델명별 1회 로드 후 재사용, 동기 predict를 asyncio.to_thread로 감쌈
"""

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

# 문서 절단 상한(토큰), 과대 입력이 추론을 느리게·불안정하게 만들지 않도록 가드
_MAX_LENGTH = 512

# 모델명별 CrossEncoder 캐시, 임포트·가중치 로드가 무거워 최초 사용 시 1회 생성
_models: dict[str, "CrossEncoder"] = {}


def _get_model(name: str) -> "CrossEncoder":
    """모델명 기준 CrossEncoder 지연 로드 후 재사용"""
    model = _models.get(name)
    if model is None:
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(name, device="cpu", max_length=_MAX_LENGTH)
        _models[name] = model
    return model


class CrossEncoderReranker:
    """Reranker 포트의 CrossEncoder 구현"""

    def __init__(self, model: str) -> None:
        self._model_name = model

    def _score(self, query: str, contents: list[str]) -> list[float]:
        """(query, 문서) 쌍 관련도 점수 (동기, 스레드에서 호출)"""
        model = _get_model(self._model_name)
        scores = model.predict([(query, content) for content in contents])
        return [float(score) for score in scores]

    async def rerank(
        self, query: str, documents: list[dict[str, Any]], top_k: int
    ) -> list[dict[str, Any]]:
        """query 기준 재정렬 후 상위 top_k 반환, score를 cross-encoder 관련도 점수로 갱신"""
        if not documents:
            return []
        contents = [str(doc.get("content", "")) for doc in documents]
        scores = await asyncio.to_thread(self._score, query, contents)
        ranked = sorted(zip(documents, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        return [{**doc, "score": score} for doc, score in ranked[:top_k]]

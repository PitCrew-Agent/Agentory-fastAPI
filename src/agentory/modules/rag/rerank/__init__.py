"""리랭커 어댑터 패키지, provider 설정으로 구현 선택 (교체 가능 지점 ③)

get_reranker가 settings.reranker_provider로 어댑터 디스패치
bge=고품질(기본)·minilm=경량(CPU 지연 제약)·none=리랭크 미적용
"""

from agentory.core.config import get_settings
from agentory.modules.rag.rerank.base import Reranker

# provider별 기본 CrossEncoder 모델, reranker_model로 개별 오버라이드 가능
_DEFAULT_MODELS = {
    "bge": "BAAI/bge-reranker-v2-m3",
    "minilm": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
}


def get_reranker() -> Reranker | None:
    """settings.reranker_provider 기반 리랭커 반환, none이면 None(재정렬 미적용)"""
    provider = (get_settings().reranker_provider or "minilm").lower()
    if provider == "none":
        return None
    if provider in _DEFAULT_MODELS:
        from agentory.modules.rag.rerank.cross_encoder import CrossEncoderReranker

        model = get_settings().reranker_model or _DEFAULT_MODELS[provider]
        return CrossEncoderReranker(model)
    raise ValueError(f"지원하지 않는 reranker_provider: {provider}")

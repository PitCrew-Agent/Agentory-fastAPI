"""리랭커 단위 테스트 (AI_RAG02_RERANK01)

CrossEncoder 실제 로드 없이 fake 모델로 재정렬 로직·팩토리 디스패치 검증
"""

import pytest

from agentory.modules.rag.rerank import _DEFAULT_MODELS, get_reranker
from agentory.modules.rag.rerank.cross_encoder import CrossEncoderReranker


class _FakeCE:
    # predict가 주입된 점수를 그대로 반환하는 fake CrossEncoder
    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def predict(self, pairs):
        assert len(pairs) == len(self._scores)
        return self._scores


class _Settings:
    def __init__(self, provider: str, model: str = "") -> None:
        self.reranker_provider = provider
        self.reranker_model = model


def _docs() -> list[dict]:
    return [
        {"doc_id": "A", "content": "낮음", "score": 0.90},
        {"doc_id": "B", "content": "높음", "score": 0.50},
        {"doc_id": "C", "content": "중간", "score": 0.70},
    ]


# ------------------------------------------------------------------ 재정렬 로직
async def test_rerank_reorders_by_ce_score_and_truncates(monkeypatch):
    # CE 점수 A=0.1, B=0.9, C=0.5 → 재정렬 순서 B, C, A, top_k=2 → B, C
    monkeypatch.setattr(
        "agentory.modules.rag.rerank.cross_encoder._get_model",
        lambda name: _FakeCE([0.1, 0.9, 0.5]),
    )
    out = await CrossEncoderReranker("fake").rerank("q", _docs(), top_k=2)
    assert [d["doc_id"] for d in out] == ["B", "C"]
    assert out[0]["score"] == 0.9  # score를 cross-encoder 점수로 갱신
    assert out[1]["score"] == 0.5
    assert out[0]["content"] == "높음"  # doc_id·content 보존


async def test_rerank_empty_returns_empty():
    assert await CrossEncoderReranker("fake").rerank("q", [], top_k=3) == []


async def test_rerank_top_k_larger_than_docs(monkeypatch):
    monkeypatch.setattr(
        "agentory.modules.rag.rerank.cross_encoder._get_model",
        lambda name: _FakeCE([0.3, 0.1, 0.2]),
    )
    out = await CrossEncoderReranker("fake").rerank("q", _docs(), top_k=10)
    assert [d["doc_id"] for d in out] == ["A", "C", "B"]  # 전체 반환, 정렬만


# ------------------------------------------------------------------ 팩토리 디스패치
def test_get_reranker_none(monkeypatch):
    monkeypatch.setattr("agentory.modules.rag.rerank.get_settings", lambda: _Settings("none"))
    assert get_reranker() is None


def test_get_reranker_bge_default_model(monkeypatch):
    monkeypatch.setattr("agentory.modules.rag.rerank.get_settings", lambda: _Settings("BGE"))
    rr = get_reranker()
    assert isinstance(rr, CrossEncoderReranker)
    assert rr._model_name == _DEFAULT_MODELS["bge"]


def test_get_reranker_minilm(monkeypatch):
    monkeypatch.setattr("agentory.modules.rag.rerank.get_settings", lambda: _Settings("minilm"))
    assert get_reranker()._model_name == _DEFAULT_MODELS["minilm"]


def test_get_reranker_model_override(monkeypatch):
    monkeypatch.setattr(
        "agentory.modules.rag.rerank.get_settings", lambda: _Settings("bge", "custom/model")
    )
    assert get_reranker()._model_name == "custom/model"


def test_get_reranker_invalid_provider(monkeypatch):
    monkeypatch.setattr("agentory.modules.rag.rerank.get_settings", lambda: _Settings("bogus"))
    with pytest.raises(ValueError):
        get_reranker()

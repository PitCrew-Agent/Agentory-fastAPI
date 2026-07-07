"""MCP knowledge 매뉴얼 검색 단위 테스트 (BE_MCP04_RAG01)

DB·임베딩 없이 임계값 필터와 파라미터 검증, 설정 반영을 확인
"""

import pytest

from agentory.core.config import Settings
from mcp_knowledge import server
from mcp_knowledge.server import _above_threshold, search_manuals


def _result(doc_id: str, score: float) -> dict:
    return {"doc_id": doc_id, "content": "본문", "score": score}


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]


class _FakeStore:
    # search 호출 인자 기록 + 고정 결과 반환 (설정 반영 검증용)
    def __init__(self, results: list[dict]):
        self._results = results
        self.called_with: dict | None = None

    async def search(self, embedding, *, top_k, equipment_type=None):
        self.called_with = {"top_k": top_k, "equipment_type": equipment_type}
        return self._results


def _patch_pipeline(monkeypatch, store: _FakeStore, settings: Settings) -> None:
    monkeypatch.setattr(server, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(server, "PgVectorStore", lambda: store)
    monkeypatch.setattr(server, "get_settings", lambda: settings)


def test_above_threshold_keeps_scores_at_or_above():
    results = [_result("MAN-ETC-042", 0.9), _result("MAN-ETC-043", 0.5)]
    assert _above_threshold(results, threshold=0.3) == results


def test_above_threshold_drops_low_scores():
    results = [_result("MAN-ETC-042", 0.9), _result("MAN-ETC-043", 0.1)]
    filtered = _above_threshold(results, threshold=0.3)
    assert [item["doc_id"] for item in filtered] == ["MAN-ETC-042"]


def test_above_threshold_all_below_returns_empty():
    results = [_result("MAN-ETC-042", 0.1), _result("MAN-ETC-043", 0.05)]
    assert _above_threshold(results, threshold=0.3) == []


def test_above_threshold_boundary_is_inclusive():
    results = [_result("MAN-ETC-042", 0.3)]
    assert _above_threshold(results, threshold=0.3) == results


def test_above_threshold_empty_input_returns_empty():
    assert _above_threshold([], threshold=0.3) == []


def test_min_score_default_within_unit_range():
    settings = Settings(_env_file=None)
    assert 0.0 < settings.rag_search_min_score < 1.0


async def test_search_manuals_rejects_empty_query():
    with pytest.raises(ValueError):
        await search_manuals(query="   ")


async def test_search_manuals_rejects_top_k_below_one(monkeypatch):
    store = _FakeStore([])
    _patch_pipeline(monkeypatch, store, Settings(_env_file=None))
    with pytest.raises(ValueError):
        await search_manuals(query="ERR-402 조치", top_k=0)


async def test_search_manuals_uses_settings_defaults(monkeypatch):
    # top_k 미지정 시 설정 top_k 사용, min_score 미달 결과 제외
    store = _FakeStore([_result("MAN-ETC-042", 0.9), _result("MAN-ETC-043", 0.4)])
    settings = Settings(_env_file=None, rag_search_top_k=5, rag_search_min_score=0.5)
    _patch_pipeline(monkeypatch, store, settings)
    results = await search_manuals(query="ERR-402 냉각 이상 조치")
    assert store.called_with == {"top_k": 5, "equipment_type": None}
    assert [item["doc_id"] for item in results] == ["MAN-ETC-042"]


async def test_search_manuals_explicit_top_k_overrides_settings(monkeypatch):
    store = _FakeStore([])
    settings = Settings(_env_file=None, rag_search_top_k=5)
    _patch_pipeline(monkeypatch, store, settings)
    results = await search_manuals(query="WRN-501 가스 유량", top_k=7)
    assert store.called_with == {"top_k": 7, "equipment_type": None}
    assert results == []


async def test_search_manuals_all_below_threshold_returns_empty(monkeypatch):
    # 전부 임계값 미달이면 빈 배열 (관련 문서 없음, 환각 방지)
    store = _FakeStore([_result("MAN-ETC-042", 0.1)])
    settings = Settings(_env_file=None, rag_search_min_score=0.2)
    _patch_pipeline(monkeypatch, store, settings)
    assert await search_manuals(query="ERR-999 조치") == []

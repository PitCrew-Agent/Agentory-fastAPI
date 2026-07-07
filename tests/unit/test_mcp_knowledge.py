"""MCP knowledge 임계값 필터 단위 테스트 (BE_MCP04_RAG01)

DB·임베딩 없이 순수 임계값 로직만 검증
"""

from mcp_knowledge.server import MIN_SCORE, _above_threshold


def _result(doc_id: str, score: float) -> dict:
    return {"doc_id": doc_id, "content": "본문", "score": score}


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
    assert 0.0 < MIN_SCORE < 1.0

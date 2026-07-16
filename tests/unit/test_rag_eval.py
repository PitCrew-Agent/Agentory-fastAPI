"""RAG 검색 품질 평가 단위 테스트 (AI_RAG02_EVAL01)"""

import pytest

from agentory.modules.rag.eval import (
    aggregate,
    evaluate_retrieval,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
)


class FakeEmbedder:
    def __init__(self) -> None:
        self.inputs: list[list[str]] = []
        self.query_inputs: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.inputs.append(texts)
        return [[float(len(text))] for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.query_inputs.append(text)
        return [float(len(text))]


class FakeStore:
    def __init__(self, results: list[dict]) -> None:
        self.results = results
        self.calls: list[dict] = []

    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 3,
        equipment_type: str | None = None,
    ) -> list[dict]:
        self.calls.append(
            {
                "query_embedding": query_embedding,
                "top_k": top_k,
                "equipment_type": equipment_type,
            }
        )
        return self.results[:top_k]


def test_hit_at_k_returns_one_when_expected_doc_is_in_top_k():
    assert hit_at_k({"MAN-1"}, ["MAN-2", "MAN-1", "MAN-3"], k=2) == 1.0


def test_hit_at_k_returns_zero_when_expected_doc_is_outside_top_k():
    assert hit_at_k({"MAN-1"}, ["MAN-2", "MAN-3", "MAN-1"], k=2) == 0.0


def test_hit_at_k_returns_zero_for_empty_expected_docs():
    assert hit_at_k(set(), ["MAN-1"], k=1) == 0.0


def test_recall_at_k_returns_partial_ratio():
    assert recall_at_k({"MAN-1", "MAN-2", "MAN-3"}, ["MAN-2", "MAN-3", "MAN-4"], k=2) == (2 / 3)


def test_recall_at_k_returns_one_when_all_expected_docs_are_found():
    assert recall_at_k({"MAN-1", "MAN-2"}, ["MAN-2", "MAN-1", "MAN-3"], k=3) == 1.0


def test_recall_at_k_returns_zero_for_empty_expected_docs():
    assert recall_at_k(set(), ["MAN-1"], k=1) == 0.0


def test_reciprocal_rank_returns_one_for_first_rank():
    assert reciprocal_rank({"MAN-1"}, ["MAN-1", "MAN-2"]) == 1.0


def test_reciprocal_rank_returns_inverse_rank_for_later_match():
    assert reciprocal_rank({"MAN-1"}, ["MAN-2", "MAN-3", "MAN-1"]) == 1 / 3


def test_reciprocal_rank_returns_zero_without_match():
    assert reciprocal_rank({"MAN-1"}, ["MAN-2", "MAN-3"]) == 0.0


def test_aggregate_returns_average_metrics():
    report = aggregate(
        [
            {"hit": 1.0, "recall": 0.5, "rr": 1.0},
            {"hit": 0.0, "recall": 0.0, "rr": 0.0},
        ],
        k=3,
    )

    assert report == {"n": 2, "hit@k": 0.5, "recall@k": 0.25, "mrr": 0.5, "k": 3}


def test_aggregate_returns_zero_metrics_for_empty_cases():
    assert aggregate([], k=3) == {"n": 0, "hit@k": 0.0, "recall@k": 0.0, "mrr": 0.0, "k": 3}


@pytest.mark.parametrize(
    ("func", "args"),
    [
        (hit_at_k, ({"MAN-1"}, ["MAN-1"], 0)),
        (recall_at_k, ({"MAN-1"}, ["MAN-1"], 0)),
        (aggregate, ([], 0)),
    ],
)
def test_k_must_be_positive(func, args):
    with pytest.raises(ValueError, match="k는 1 이상"):
        func(*args)


async def test_evaluate_retrieval_scores_only_cases_with_rag_hit_docs():
    embedder = FakeEmbedder()
    store = FakeStore(
        [
            {"doc_id": "MAN-2", "content": "다른 문서", "score": 0.9},
            {"doc_id": "MAN-1", "content": "기대 문서", "score": 0.8},
            {"doc_id": "MAN-3", "content": "제외 문서", "score": 0.7},
        ]
    )
    cases = [
        {"id": "SKIP-001", "query": "미대상 질의", "expect": {}},
        {
            "id": "GOLDEN-001",
            "query": "평가 질의",
            "expect": {"rag_hit_docs": ["MAN-1"]},
        },
    ]

    report = await evaluate_retrieval(cases, embedder=embedder, store=store, k=2)

    assert embedder.query_inputs == ["평가 질의"]
    assert store.calls == [{"query_embedding": [5.0], "top_k": 2, "equipment_type": None}]
    assert report["n"] == 1
    assert report["hit@k"] == 1.0
    assert report["recall@k"] == 1.0
    assert report["mrr"] == 0.5
    assert report["per_case"] == [
        {
            "id": "GOLDEN-001",
            "query": "평가 질의",
            "expected": ["MAN-1"],
            "retrieved": ["MAN-2", "MAN-1"],
            "hit": 1.0,
            "recall": 1.0,
            "rr": 0.5,
        }
    ]


async def test_evaluate_retrieval_applies_min_score_filter():
    # 운영 search_manuals의 임계값 필터를 평가에 재현 (BE_MCP04_RAG01)
    embedder = FakeEmbedder()
    store = FakeStore(
        [
            {"doc_id": "MAN-2", "content": "다른 문서", "score": 0.9},
            {"doc_id": "MAN-1", "content": "기대 문서", "score": 0.3},
        ]
    )
    cases = [{"id": "GOLDEN-001", "query": "평가 질의", "expect": {"rag_hit_docs": ["MAN-1"]}}]

    unfiltered = await evaluate_retrieval(cases, embedder=embedder, store=store, k=2)
    filtered = await evaluate_retrieval(cases, embedder=embedder, store=store, k=2, min_score=0.5)

    assert unfiltered["hit@k"] == 1.0
    assert filtered["hit@k"] == 0.0
    assert filtered["per_case"][0]["retrieved"] == ["MAN-2"]

"""RAG 검색 품질 평가 하네스 (AI_RAG02_EVAL01)

골든 질의 셋의 rag_hit_docs 기준 검색 랭킹 품질 수치화
지표는 순수 함수, evaluate_retrieval만 embedder와 store 주입형 I/O
"""

from typing import Any

from agentory.modules.rag.embedding.base import Embedder
from agentory.modules.rag.store.base import VectorStore


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("k는 1 이상이어야 합니다")


def hit_at_k(expected: set[str], retrieved: list[str], k: int) -> float:
    """기대 문서 중 하나라도 top-k에 있으면 1 반환"""
    _validate_k(k)
    if not expected:
        return 0.0
    return 1.0 if expected & set(retrieved[:k]) else 0.0


def recall_at_k(expected: set[str], retrieved: list[str], k: int) -> float:
    """기대 문서 중 top-k에 회수된 비율"""
    _validate_k(k)
    if not expected:
        return 0.0
    found = expected & set(retrieved[:k])
    return len(found) / len(expected)


def reciprocal_rank(expected: set[str], retrieved: list[str]) -> float:
    """첫 기대 문서의 순위 역수"""
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in expected:
            return 1.0 / rank
    return 0.0


def evaluate_case(expected: set[str], retrieved: list[str], k: int) -> dict[str, float]:
    """케이스별 hit·recall·rr 계산"""
    return {
        "hit": hit_at_k(expected, retrieved, k),
        "recall": recall_at_k(expected, retrieved, k),
        "rr": reciprocal_rank(expected, retrieved),
    }


def aggregate(per_case: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """케이스별 결과를 hit@k·recall@k·MRR 평균으로 집계"""
    _validate_k(k)
    n = len(per_case)
    if n == 0:
        return {"n": 0, "hit@k": 0.0, "recall@k": 0.0, "mrr": 0.0, "k": k}
    return {
        "n": n,
        "hit@k": sum(case["hit"] for case in per_case) / n,
        "recall@k": sum(case["recall"] for case in per_case) / n,
        "mrr": sum(case["rr"] for case in per_case) / n,
        "k": k,
    }


async def evaluate_retrieval(
    cases: list[dict[str, Any]],
    *,
    embedder: Embedder,
    store: VectorStore,
    k: int = 3,
) -> dict[str, Any]:
    """골든 케이스로 검색 랭킹 품질 평가, rag_hit_docs 있는 케이스만 대상

    각 케이스: query 임베딩, store.search(top_k=k), doc_id 추출, 지표 계산
    반환: aggregate 결과 + per_case 상세
    """
    _validate_k(k)
    per_case: list[dict[str, Any]] = []
    for case in cases:
        expected_docs = [str(doc_id) for doc_id in case.get("expect", {}).get("rag_hit_docs") or []]
        expected = set(expected_docs)
        if not expected:
            continue
        embeddings = await embedder.embed([case["query"]])
        results = await store.search(embeddings[0], top_k=k)
        retrieved = [str(item["doc_id"]) for item in results]
        per_case.append(
            {
                "id": str(case.get("id", "?")),
                "query": case["query"],
                "expected": expected_docs,
                "retrieved": retrieved,
                **evaluate_case(expected, retrieved, k),
            }
        )
    report = aggregate(per_case, k)
    report["per_case"] = per_case
    return report

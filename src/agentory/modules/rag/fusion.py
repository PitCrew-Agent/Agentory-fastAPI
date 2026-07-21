"""Dense·어휘 후보 융합, convex 결합 (AI_RAG02_HYBRID01)

두 검색기의 점수 척도가 다르므로(코사인 0~1, BM25 무제한) min-max 정규화 후 가중 결합
convex 선정 근거는 alpha로 신호 비중을 직접 조절할 수 있다는 점 (RAG 아키텍처 실험 결정 기록 5.2)

alpha는 dense 비중, 1.0이면 dense 단독과 동일
한쪽에만 등장한 후보는 다른 쪽 점수를 0으로 두어 등장 리스트 수가 많을수록 유리
"""

from typing import Any

# 융합 식별 키, doc_id는 문서 단위라 청크를 구분하지 못함
_KEY = "chunk_id"
# 정규화 하한, min-max는 최저 점수를 0으로 만들어 미등장 후보와 구분되지 않음
# 하한을 두어야 alpha=1.0이 dense 단독과 정확히 같은 순서로 환원됨
_PRESENT_FLOOR = 1e-6


def minmax(scores: dict[Any, float]) -> dict[Any, float]:
    """0~1 정규화, 전 항목이 동점이면 1.0으로 통일"""
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi <= lo:
        return dict.fromkeys(scores, 1.0)
    return {key: (value - lo) / (hi - lo) for key, value in scores.items()}


def _normalize_present(items: list[dict[str, Any]]) -> dict[Any, float]:
    """등장 후보를 하한 이상으로 정규화, 미등장(0.0)과 구분되게 함"""
    normalized = minmax({item[_KEY]: float(item["score"]) for item in items})
    return {key: max(value, _PRESENT_FLOOR) for key, value in normalized.items()}


def convex_fuse(
    dense: list[dict[str, Any]],
    sparse: list[dict[str, Any]],
    *,
    alpha: float,
    top_k: int,
) -> list[dict[str, Any]]:
    """convex 결합 후 상위 top_k, 반환 항목의 score는 융합 점수로 갱신

    alpha x dense + (1-alpha) x sparse, 각 점수는 min-max 정규화 선행
    반환 score는 코사인이 아니므로 코사인 임계값 기준으로 재사용 금지
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha는 0 이상 1 이하여야 함: {alpha}")
    if not dense:
        return sparse[:top_k]
    if not sparse:
        return dense[:top_k]

    dense_norm = _normalize_present(dense)
    sparse_norm = _normalize_present(sparse)
    by_key = {item[_KEY]: item for item in sparse}
    by_key.update({item[_KEY]: item for item in dense})  # 본문은 dense 항목 우선

    fused = {
        key: alpha * dense_norm.get(key, 0.0) + (1.0 - alpha) * sparse_norm.get(key, 0.0)
        for key in by_key
    }
    order = sorted(fused, key=lambda key: fused[key], reverse=True)[:top_k]
    return [{**by_key[key], "score": fused[key]} for key in order]

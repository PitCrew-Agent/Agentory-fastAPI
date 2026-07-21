"""하이브리드 검색 단위 테스트 (AI_RAG02_HYBRID01)

어휘 검색·convex 융합의 순수 로직과 예외 처리 검증
DB·모델 의존 없이 동작하도록 청크 소스는 스텁 사용
"""

import pytest

from agentory.modules.rag import lexical
from agentory.modules.rag.fusion import convex_fuse, minmax
from agentory.modules.rag.lexical import Bm25Index, tokenize


def _chunk(chunk_id: int, content: str, doc_id: str = "MAN-TM-8600") -> dict:
    return {"chunk_id": chunk_id, "doc_id": doc_id, "content": content}


def _scored(chunk_id: int, score: float, doc_id: str = "MAN-TM-8600") -> dict:
    return {"chunk_id": chunk_id, "doc_id": doc_id, "content": f"본문{chunk_id}", "score": score}


class _StubSource:
    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks
        self.calls = 0

    async def fetch_all(self) -> list[dict]:
        self.calls += 1
        return self._chunks


# ---------------------------------------------------------------- 토크나이저
def test_whitespace_keeps_particle():
    # 어절 분리는 조사가 붙어 남음, 정확 매칭이 약해지는 원인
    assert tokenize("ERR-402는 온도가", "whitespace") == ["ERR-402는", "온도가"]


def test_kiwi_preserves_alarm_code():
    # 정규식 선분리로 코드 원형 보존, 조사는 형태소 분석에서 제거
    tokens = tokenize("ERR-402는 온도가 상승", "kiwi")
    assert "err-402" in tokens
    assert "온도" in tokens
    assert "온도가" not in tokens


def test_bigram_preserves_alarm_code():
    tokens = tokenize("ERR-402는 온도", "bigram")
    assert "err-402" in tokens


def test_unknown_tokenizer_raises():
    with pytest.raises(ValueError, match="지원하지 않는 토크나이저"):
        tokenize("본문", "mecab")


# ---------------------------------------------------------------- BM25 색인
def test_bm25_ranks_exact_code_first():
    chunks = [
        _chunk(1, "WRN-701 온도 드리프트 대응 절차"),
        _chunk(2, "ERR-402 냉각 계통 고장 발생 조건"),
        _chunk(3, "설비 일반 점검 주기 안내"),
    ]
    results = Bm25Index(chunks, "kiwi").search("ERR-402 발생 조건", top_k=2)
    assert results[0]["chunk_id"] == 2
    assert len(results) == 2


def test_bm25_empty_corpus_returns_empty():
    assert Bm25Index([], "whitespace").search("ERR-402", top_k=5) == []


def test_bm25_non_positive_top_k_returns_empty():
    index = Bm25Index([_chunk(1, "ERR-402 냉각 고장")], "whitespace")
    assert index.search("ERR-402", top_k=0) == []


# ---------------------------------------------------------------- 정규화
def test_minmax_scales_to_unit_range():
    assert minmax({"a": 1.0, "b": 3.0, "c": 2.0}) == {"a": 0.0, "b": 1.0, "c": 0.5}


def test_minmax_all_equal_returns_one():
    # 동점이면 분모가 0이라 1.0으로 통일, 0으로 나누기 방지
    assert minmax({"a": 2.0, "b": 2.0}) == {"a": 1.0, "b": 1.0}


def test_minmax_empty_returns_empty():
    assert minmax({}) == {}


# ---------------------------------------------------------------- convex 융합
def test_convex_alpha_one_keeps_dense_order():
    # alpha=1.0이면 dense 단독과 동일 순서, 하이브리드 off 회귀 기준
    dense = [_scored(1, 0.9), _scored(2, 0.5)]
    sparse = [_scored(3, 10.0), _scored(2, 1.0)]
    fused = convex_fuse(dense, sparse, alpha=1.0, top_k=2)
    assert [item["chunk_id"] for item in fused] == [1, 2]


def test_convex_alpha_zero_follows_sparse():
    dense = [_scored(1, 0.9), _scored(2, 0.5)]
    sparse = [_scored(3, 10.0), _scored(2, 1.0)]
    fused = convex_fuse(dense, sparse, alpha=0.0, top_k=1)
    assert fused[0]["chunk_id"] == 3


def test_convex_promotes_chunk_in_both_lists():
    # 양쪽 모두에 등장한 후보가 한쪽에만 있는 후보보다 유리
    dense = [_scored(1, 0.9), _scored(2, 0.6)]
    sparse = [_scored(2, 9.0), _scored(3, 8.0)]
    fused = convex_fuse(dense, sparse, alpha=0.5, top_k=3)
    assert fused[0]["chunk_id"] == 2


def test_convex_truncates_to_top_k():
    dense = [_scored(1, 0.9), _scored(2, 0.6)]
    sparse = [_scored(3, 9.0), _scored(4, 8.0)]
    assert len(convex_fuse(dense, sparse, alpha=0.5, top_k=2)) == 2


def test_convex_empty_sparse_returns_dense():
    dense = [_scored(1, 0.9)]
    assert convex_fuse(dense, [], alpha=0.5, top_k=3) == dense


def test_convex_empty_dense_returns_sparse():
    sparse = [_scored(1, 9.0)]
    assert convex_fuse([], sparse, alpha=0.5, top_k=3) == sparse


def test_convex_score_replaced_by_fused_value():
    # 반환 score는 융합 점수라 코사인 임계값 기준으로 재사용 불가
    dense = [_scored(1, 0.9), _scored(2, 0.1)]
    fused = convex_fuse(dense, [_scored(1, 5.0)], alpha=0.5, top_k=1)
    assert fused[0]["score"] != 0.9


@pytest.mark.parametrize("alpha", [-0.1, 1.1])
def test_convex_alpha_out_of_range_raises(alpha):
    with pytest.raises(ValueError, match="alpha는 0 이상 1 이하여야 함"):
        convex_fuse([_scored(1, 0.9)], [_scored(2, 1.0)], alpha=alpha, top_k=1)


# ---------------------------------------------------------------- 색인 캐시
@pytest.mark.asyncio
async def test_index_is_cached_between_calls():
    lexical.reset_index()
    source = _StubSource([_chunk(1, "ERR-402 냉각 고장")])
    await lexical.get_index(source, "whitespace")
    await lexical.get_index(source, "whitespace")
    assert source.calls == 1
    lexical.reset_index()


@pytest.mark.asyncio
async def test_index_rebuilds_when_tokenizer_changes():
    lexical.reset_index()
    source = _StubSource([_chunk(1, "ERR-402 냉각 고장")])
    await lexical.get_index(source, "whitespace")
    await lexical.get_index(source, "bigram")
    assert source.calls == 2
    lexical.reset_index()


@pytest.mark.asyncio
async def test_reset_index_forces_rebuild():
    lexical.reset_index()
    source = _StubSource([_chunk(1, "ERR-402 냉각 고장")])
    await lexical.get_index(source, "whitespace")
    lexical.reset_index()
    await lexical.get_index(source, "whitespace")
    assert source.calls == 2
    lexical.reset_index()

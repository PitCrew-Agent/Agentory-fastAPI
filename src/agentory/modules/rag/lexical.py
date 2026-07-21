"""BM25 어휘 검색, 하이브리드 1차 검색기 (AI_RAG02_HYBRID01)

Dense 검색이 놓치는 알람코드·장비명 등 문자 일치 신호를 회수
포트를 두지 않는 이유는 구현 후보가 BM25 하나뿐이고 전환은 config 스위치로 충분하기 때문

토크나이저는 config로 선택 (2026-07-21 실험 기록 참고)
  whitespace 어절 분리, 조사가 붙어 정확 매칭이 약하나 리랭커 병용 시 최고 성능
  kiwi       형태소 분리, 리랭커 없는 조건에서 ndcg@3 +4.1%p 이나 병용 시 열세
  bigram     문자 2-gram, 의존성 없이 조사 문제 회피

색인은 프로세스 단위 1회 구축 후 재사용
재적재 후에는 reset_index 호출 또는 서버 재기동 필요 (별도 프로세스 적재 시 갱신 불가)
"""

import logging
import re
from typing import Any, Protocol

log = logging.getLogger(__name__)

# 영숫자 코드·영문 변수명, 형태소 분석 전 원형 보존이 필요한 토큰
# ERR-402 / MPS-8600 / EQP-ETCH-SOP-001 / gas_flow / temperature 대응
_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+|[A-Za-z][A-Za-z0-9_]{2,}")
# Kiwi 실질 형태소, 조사(J*)·어미(E*)·접사(XS*)는 제외해 조사 결합 불일치 해소
_KIWI_KEEP = ("NN", "VV", "VA", "XR", "SN", "MAG")

_kiwi = None
_index: "Bm25Index | None" = None


class ChunkSource(Protocol):
    async def fetch_all(self) -> list[dict[str, Any]]:
        """색인 대상 전체 청크, 반환: [{chunk_id, doc_id, content}]"""
        ...


def _get_kiwi():
    """Kiwi 지연 로드 후 재사용, 가중치 로드가 무거워 최초 사용 시 1회 생성

    kiwipiepy는 기본 의존성이 아님, 모델 105MB라 kiwi 토크나이저 채택 시에만 설치
    """
    global _kiwi
    if _kiwi is None:
        try:
            from kiwipiepy import Kiwi
        except ImportError as exc:
            raise RuntimeError(
                "HYBRID_TOKENIZER=kiwi에는 kiwipiepy 설치 필요, uv add kiwipiepy"
            ) from exc

        _kiwi = Kiwi()
    return _kiwi


def _tok_whitespace(text: str) -> list[str]:
    """어절(공백) 분리"""
    return text.split()


def _tok_kiwi(text: str) -> list[str]:
    """코드 정규식 선분리 후 나머지를 Kiwi 형태소 분석

    Kiwi 단독으로는 ERR-402가 ERR / - / 402로 분해되므로 코드를 먼저 빼냄
    """
    codes = [m.group(0).lower() for m in _CODE_RE.finditer(text)]
    stripped = _CODE_RE.sub(" ", text)
    morphs = [t.form for t in _get_kiwi().tokenize(stripped) if t.tag.startswith(_KIWI_KEEP)]
    return codes + morphs


def _tok_bigram(text: str) -> list[str]:
    """문자 2-gram, 코드는 원형 보존 후 나머지만 분해"""
    codes = [m.group(0).lower() for m in _CODE_RE.finditer(text)]
    stripped = re.sub(r"\s+", "", _CODE_RE.sub(" ", text))
    return codes + [stripped[i : i + 2] for i in range(len(stripped) - 1)]


TOKENIZERS = {"whitespace": _tok_whitespace, "kiwi": _tok_kiwi, "bigram": _tok_bigram}


def tokenize(text: str, tokenizer: str) -> list[str]:
    """설정된 방식으로 토큰 분리, 색인과 질의에 반드시 동일 방식 적용"""
    fn = TOKENIZERS.get(tokenizer)
    if fn is None:
        raise ValueError(f"지원하지 않는 토크나이저: {tokenizer}")
    return fn(text)


class Bm25Index:
    """BM25 역색인, 청크 본문을 토큰화해 어휘 점수 산출"""

    def __init__(self, chunks: list[dict[str, Any]], tokenizer: str) -> None:
        self._chunks = chunks
        self._tokenizer = tokenizer
        self._bm25 = None
        if chunks:
            # 빈 코퍼스로 BM25Okapi를 만들면 평균 문서 길이 계산에서 ZeroDivisionError
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi([tokenize(c["content"], tokenizer) for c in chunks])

    def __len__(self) -> int:
        return len(self._chunks)

    def search(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        """어휘 점수 상위 top_k, 반환: [{chunk_id, doc_id, content, score}]

        점수는 BM25 원점수이며 코사인과 척도가 달라 융합 전 정규화 필요
        """
        if self._bm25 is None or top_k <= 0:
            return []
        scores = self._bm25.get_scores(tokenize(query, self._tokenizer))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [{**self._chunks[i], "score": float(scores[i])} for i in order]


async def get_index(source: ChunkSource, tokenizer: str) -> Bm25Index:
    """색인 지연 구축 후 재사용, 토크나이저가 바뀌면 재구축"""
    global _index
    if _index is None or _index._tokenizer != tokenizer:
        chunks = await source.fetch_all()
        _index = Bm25Index(chunks, tokenizer)
        log.info("BM25 색인 구축 완료: %d청크, 토크나이저=%s", len(_index), tokenizer)
    return _index


def reset_index() -> None:
    """색인 캐시 무효화, 재적재 직후 호출"""
    global _index
    _index = None

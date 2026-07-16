"""매뉴얼 인제스트 파이프라인 (AI_RAG01_PREP01 / AI_RAG01_CHUNK01)

파싱 → 정규화 → 청킹 → 임베딩 → 적재
실행은 scripts/ingest_manuals.py에서 이 파이프라인 호출
PDF·DOCX·MD는 Docling 변환 후 구조 인식 HybridChunker로 분할, txt는 토큰 분할 폴백
Docling 선정 근거: 표 구조 보존·러닝 헤더/푸터 자동 제외·알람코드 무손실 (2단계 평가 실측)
청커 선정 근거: HybridChunker max_tokens=512, 2단계 검색 평가에서 ndcg@3·mrr 우위
"""

import re
import unicodedata
from pathlib import Path
from typing import Any

from agentory.modules.rag.embedding.base import Embedder
from agentory.modules.rag.store.base import VectorStore

# Docling HybridChunker 토큰 상한, 2단계 검색 평가 선정값
DEFAULT_MAX_TOKENS = 512

# 지원 입력 형식, 그 외 확장자는 파라미터 오류
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})

# 파서(parse) 단계 Docling 대상, txt·md는 평문 디코드
_DOCLING_SUFFIXES = frozenset({".pdf", ".docx"})

# 청킹 단계 Docling 대상, md도 헤딩·표 구조 인식 위해 Docling 경유 (2단계 검증 레시피)
_CHUNK_DOCLING_SUFFIXES = frozenset({".pdf", ".docx", ".md"})

# 제로폭 문자·소프트하이픈 삭제 매핑 (soft hyphen, ZWSP, ZWNJ, ZWJ, BOM)
_ZERO_WIDTH_DELETION = dict.fromkeys((0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF), None)
# 개행(\n)·탭(\t) 제외 C0/C1 제어문자
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# 줄바꿈으로 끊긴 라틴 단어, 양쪽이 라틴 글자일 때만 연결
_DEHYPHEN_RE = re.compile(r"([A-Za-z])-\n([A-Za-z])")

# Docling 변환기·청커·tiktoken 인코딩 캐시, 임포트·모델 로드가 무거워 최초 사용 시 1회 생성
_converter = None
_encoding = None
_chunkers: dict[int, Any] = {}


def parse(path: Path) -> str:
    """파일 → 원시 텍스트 추출, 확장자 디스패치 (I/O)"""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"지원하지 않는 확장자: {path.suffix}")
    if suffix in _DOCLING_SUFFIXES:
        return _parse_docling(path)
    return _parse_text(path)  # .txt, .md


def normalize(text: str) -> str:
    """원시 텍스트 → 정규화 텍스트, 순수 함수 (AI_RAG01_PREP01)"""
    text = _normalize_newlines(text)
    text = _to_nfc(text)
    text = _strip_zero_width_and_soft_hyphen(text)
    text = _strip_control_chars(text)
    text = _dehyphenate(text)
    return _collapse_whitespace(text)


def parse_and_normalize(path: Path) -> str:
    """parse → normalize 조합 진입점, 1단계 출력 경계"""
    return normalize(parse(path))


def _get_converter():
    """Docling 변환기 지연 생성 후 재사용"""
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter

        _converter = DocumentConverter()
    return _converter


def _convert(path: Path):
    """Docling 변환 후 DoclingDocument 반환, 표·헤딩 구조 보존"""
    return _get_converter().convert(str(path)).document


def _parse_docling(path: Path) -> str:
    """Docling 변환 후 마크다운 추출, 표 구조 보존·러닝 헤더/푸터 자동 제외"""
    return _convert(path).export_to_markdown()


def _parse_text(path: Path) -> str:
    """txt·md UTF-8 디코드"""
    return path.read_text(encoding="utf-8")


def _normalize_newlines(text: str) -> str:
    """CRLF·CR → LF 통일"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _to_nfc(text: str) -> str:
    """유니코드 NFC 정규화, 한글 자모 결합"""
    return unicodedata.normalize("NFC", text)


def _strip_zero_width_and_soft_hyphen(text: str) -> str:
    """제로폭 문자·소프트하이픈 제거"""
    return text.translate(_ZERO_WIDTH_DELETION)


def _strip_control_chars(text: str) -> str:
    """개행·탭 제외 제어문자 제거"""
    return _CONTROL_RE.sub("", text)


def _dehyphenate(text: str) -> str:
    """줄바꿈으로 끊긴 라틴 단어 연결, ERR-402·A-Line 등은 보존"""
    return _DEHYPHEN_RE.sub(r"\1\2", text)


def _collapse_whitespace(text: str) -> str:
    """행말 공백·연속 공백·과다 빈 줄 축약"""
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.split("\n")]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return collapsed.strip()


def _get_encoding():
    """tiktoken cl100k 인코딩 지연 생성 후 재사용, HybridChunker 토큰 사이징 기준"""
    global _encoding
    if _encoding is None:
        import tiktoken

        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def _get_chunker(max_tokens: int):
    """HybridChunker(cl100k, max_tokens) 지연 생성 후 max_tokens별 재사용"""
    chunker = _chunkers.get(max_tokens)
    if chunker is None:
        from docling.chunking import HybridChunker
        from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer

        tokenizer = OpenAITokenizer(tokenizer=_get_encoding(), max_tokens=max_tokens)
        chunker = HybridChunker(tokenizer=tokenizer)
        _chunkers[max_tokens] = chunker
    return chunker


def _token_split(text: str, max_tokens: int) -> list[str]:
    """평문을 max_tokens 토큰 창으로 분할, Docling 미지원 txt 폴백"""
    encoding = _get_encoding()
    tokens = encoding.encode(text)
    return [encoding.decode(tokens[i : i + max_tokens]) for i in range(0, len(tokens), max_tokens)]


def chunk_document(path: Path, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[str]:
    """파일 → 청크 텍스트 목록, 확장자 디스패치 (AI_RAG01_CHUNK01)

    PDF·DOCX·MD: Docling 변환 후 HybridChunker(max_tokens) 구조 인식 분할
    txt: Docling 미지원이라 평문 토큰 분할 폴백
    각 청크에 정규화 적용, 빈 청크 제외
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens는 양수여야 함")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"지원하지 않는 확장자: {path.suffix}")
    if suffix in _CHUNK_DOCLING_SUFFIXES:
        chunker = _get_chunker(max_tokens)
        pieces = [chunk.text for chunk in chunker.chunk(_convert(path))]
    else:  # .txt
        pieces = _token_split(_parse_text(path), max_tokens)
    normalized = [normalize(piece) for piece in pieces]
    return [piece for piece in normalized if piece]


def build_chunks(
    chunk_texts: list[str],
    *,
    doc_id: str,
    equipment_type: str | None = None,
    alarm_code: str | None = None,
) -> list[dict[str, Any]]:
    """청크 텍스트 목록 → KnowledgeChunk 적재용 dict 목록, embedding 키는 임베딩 단계에서 부착"""
    return [
        {
            "doc_id": doc_id,
            "chunk_index": index,
            "equipment_type": equipment_type,
            "alarm_code": alarm_code,
            "content": piece,
        }
        for index, piece in enumerate(chunk_texts)
    ]


async def ingest_document(
    path: Path,
    *,
    doc_id: str,
    equipment_type: str | None = None,
    alarm_code: str | None = None,
    embedder: Embedder,
    store: VectorStore,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> int:
    """파싱→청킹→임베딩→적재, 반환: 적재 청크 수"""
    chunk_texts = chunk_document(path, max_tokens=max_tokens)
    chunks = build_chunks(
        chunk_texts,
        doc_id=doc_id,
        equipment_type=equipment_type,
        alarm_code=alarm_code,
    )
    if not chunks:
        return 0
    embeddings = await embedder.embed([chunk["content"] for chunk in chunks])
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        chunk["embedding"] = embedding
    return await store.upsert(chunks)

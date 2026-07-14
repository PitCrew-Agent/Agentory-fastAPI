"""매뉴얼 인제스트 파이프라인 (AI_RAG01_PREP01 / AI_RAG01_CHUNK01)

변환 → 구조 청킹 → 정규화 → 임베딩 → 적재
실행은 scripts/ingest_manuals.py에서 이 파이프라인 호출

Docling 변환으로 레이아웃·표 구조를 보존한 DoclingDocument 생성, HybridChunker가 섹션·표
경계를 존중해 분할하고 heading 맥락을 청크 앞에 부착
기존 문자 고정폭 청킹(800자·overlap 100자) 대비 절차 단계 중간 절단·표 행 분산이 사라짐
토큰 상한 1024는 임베딩 스윕(AI_RAG02_EVAL01)이 전 모델 공통 통제변수로 고정한 값
"""

import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any

from agentory.core.config import get_settings
from agentory.modules.rag.embedding.base import Embedder
from agentory.modules.rag.store.base import VectorStore

# HybridChunker 토크나이저 인코딩, max_tokens 산정 기준 (tiktoken)
_CHUNK_ENCODING = "cl100k_base"

# 지원 입력 형식, 그 외 확장자는 파라미터 오류
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})

# 제로폭 문자·소프트하이픈 삭제 매핑 (soft hyphen, ZWSP, ZWNJ, ZWJ, BOM)
_ZERO_WIDTH_DELETION = dict.fromkeys((0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF), None)
# 개행(\n)·탭(\t) 제외 C0/C1 제어문자
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# 줄바꿈으로 끊긴 라틴 단어, 양쪽이 라틴 글자일 때만 연결
_DEHYPHEN_RE = re.compile(r"([A-Za-z])-\n([A-Za-z])")

# Docling 변환기 모듈 캐시, 임포트·모델 로드가 무거워 최초 사용 시 1회 생성
_converter = None
# 토큰 상한별 HybridChunker 캐시, 토크나이저 로드 반복 방지
_chunkers: dict[int, Any] = {}


def convert_document(path: Path):
    """파일 → DoclingDocument, 확장자 디스패치 (I/O)

    반환 문서는 레이아웃·표 구조를 유지, 마크다운 export 대신 구조 청커 입력으로 사용
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"지원하지 않는 확장자: {path.suffix}")
    if suffix == ".txt":
        return _convert_text_as_markdown(path)
    return _get_converter().convert(str(path)).document


def chunk_document(doc, *, max_tokens: int) -> list[str]:
    """DoclingDocument → 구조 청크 목록, heading 맥락 접두 포함 (AI_RAG01_CHUNK01)

    max_tokens는 고정 크기가 아니라 초과 시 재분할하는 상한, overlap 개념 없음
    청크 본문은 normalize를 거쳐 제로폭 문자·행말 공백 제거
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens는 양수여야 함")
    chunker = _get_hybrid_chunker(max_tokens)
    pieces = (normalize(chunker.contextualize(chunk)) for chunk in chunker.chunk(doc))
    return [piece for piece in pieces if piece]


def normalize(text: str) -> str:
    """원시 텍스트 → 정규화 텍스트, 순수 함수 (AI_RAG01_PREP01)"""
    text = _normalize_newlines(text)
    text = _to_nfc(text)
    text = _strip_zero_width_and_soft_hyphen(text)
    text = _strip_control_chars(text)
    text = _dehyphenate(text)
    return _collapse_whitespace(text)


def build_chunk_records(
    pieces: list[str],
    *,
    doc_id: str,
    equipment_type: str | None = None,
    alarm_code: str | None = None,
) -> list[dict[str, Any]]:
    """청크 문자열 목록 → KnowledgeChunk 적재용 dict 목록, embedding 키는 임베딩 단계에서 부착"""
    return [
        {
            "doc_id": doc_id,
            "chunk_index": index,
            "equipment_type": equipment_type,
            "alarm_code": alarm_code,
            "content": piece,
        }
        for index, piece in enumerate(pieces)
    ]


async def ingest_document(
    path: Path,
    *,
    doc_id: str,
    equipment_type: str | None = None,
    alarm_code: str | None = None,
    embedder: Embedder,
    store: VectorStore,
    max_tokens: int | None = None,
) -> int:
    """변환→청킹→정규화→임베딩→적재, 반환: 적재 청크 수

    max_tokens 미지정 시 설정값(CHUNK_MAX_TOKENS) 사용
    """
    resolved_max_tokens = max_tokens or get_settings().chunk_max_tokens
    pieces = chunk_document(convert_document(path), max_tokens=resolved_max_tokens)
    chunks = build_chunk_records(
        pieces,
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


def _get_converter():
    """Docling 변환기 지연 생성 후 재사용"""
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter

        _converter = DocumentConverter()
    return _converter


def _get_hybrid_chunker(max_tokens: int):
    """토큰 상한별 HybridChunker 지연 생성 후 재사용, 구조 경계 우선 분할·인접 청크 병합

    토크나이저는 tiktoken cl100k_base, 임베딩 스윕이 통제변수로 고정한 기준과 동일
    임베더 자체 토크나이저로 바꾸면 청크 경계가 달라져 스윕 측정치를 그대로 적용할 수 없음
    """
    chunker = _chunkers.get(max_tokens)
    if chunker is None:
        import tiktoken
        from docling.chunking import HybridChunker
        from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer

        tokenizer = OpenAITokenizer(
            tokenizer=tiktoken.get_encoding(_CHUNK_ENCODING), max_tokens=max_tokens
        )
        chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)
        _chunkers[max_tokens] = chunker
    return chunker


def _convert_text_as_markdown(path: Path):
    """평문 txt를 마크다운으로 간주해 변환, Docling이 .txt 입력 형식을 지원하지 않음"""
    from docling.datamodel.base_models import DocumentStream

    stream = DocumentStream(name=f"{path.stem}.md", stream=BytesIO(path.read_bytes()))
    return _get_converter().convert(stream).document


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

"""매뉴얼 변환·정규화·구조 청킹 단위 테스트 (AI_RAG01_PREP01 / AI_RAG01_CHUNK01)

정규화는 인라인 문자열로 결정론적 검증, 변환·청킹은 tmp_path에 생성한 문서 사용
구조 청킹은 Docling HybridChunker 경유, 섹션 heading이 청크 앞에 맥락으로 붙음
특수문자는 이스케이프 대신 chr로 구성해 소스 가독성·정확성 확보
"""

import unicodedata

import pytest
from docx import Document

from agentory.modules.rag.ingest import (
    SUPPORTED_SUFFIXES,
    build_chunk_records,
    chunk_document,
    convert_document,
    normalize,
)

NL = chr(10)  # 개행
CR = chr(13)  # 캐리지리턴
TAB = chr(9)  # 탭
SOFT_HYPHEN = chr(0x00AD)  # 소프트하이픈
ZWSP = chr(0x200B)  # 제로폭 공백
NUL = chr(0)  # 널 문자
BEL = chr(7)  # 제어문자 예시

# 운영 기본 토큰 상한, 짧은 테스트 문서는 재분할 없이 1청크로 수렴
MAX_TOKENS = 1024


def _write_docx(path, blocks: list[tuple[str, str]]) -> None:
    """(유형, 텍스트) 목록으로 docx 생성, 유형은 heading | paragraph"""
    document = Document()
    for kind, text in blocks:
        if kind == "heading":
            document.add_heading(text, level=1)
        else:
            document.add_paragraph(text)
    document.save(str(path))


def test_normalize_unifies_crlf_and_cr_to_lf():
    assert normalize("a" + CR + NL + "b" + CR + "c") == "a" + NL + "b" + NL + "c"


def test_normalize_composes_hangul_to_nfc():
    decomposed = unicodedata.normalize("NFD", "설비")
    result = normalize(decomposed)
    assert unicodedata.is_normalized("NFC", result)
    assert result == "설비"


def test_normalize_removes_soft_hyphen_and_zero_width():
    assert normalize("tem" + SOFT_HYPHEN + "per" + ZWSP + "ature") == "temperature"


def test_normalize_strips_control_chars():
    assert normalize("a" + NUL + "b" + BEL + "c") == "abc"


def test_normalize_keeps_newline_and_collapses_tab_to_space():
    # 제어문자 제거 단계는 탭·개행 보존, 공백 축약 단계에서 탭은 단일 공백
    assert normalize("a" + TAB + "b" + NL + "c") == "a b" + NL + "c"


def test_normalize_dehyphenates_latin_word_across_linebreak():
    assert normalize("cool-" + NL + "down") == "cooldown"


def test_normalize_preserves_hyphen_in_codes_and_names():
    assert normalize("ERR-402 A-Line") == "ERR-402 A-Line"


def test_normalize_collapses_repeated_spaces_and_tabs():
    assert normalize("a    b" + TAB + "c") == "a b c"


def test_normalize_strips_trailing_whitespace_per_line():
    assert normalize("a  " + NL + "b  ") == "a" + NL + "b"


def test_normalize_collapses_excess_blank_lines():
    assert normalize("a" + NL * 4 + "b") == "a" + NL * 2 + "b"


def test_normalize_is_idempotent():
    messy = "제목" + CR + NL * 4 + "본문  " + TAB + "줄" + NUL + NL + "cool-" + NL + "down"
    once = normalize(messy)
    assert normalize(once) == once


def test_normalize_empty_returns_empty():
    assert normalize("") == ""


def test_normalize_whitespace_only_returns_empty():
    assert normalize("   " + NL + TAB + NL + "  ") == ""


# --- 변환 (AI_RAG01_PREP01) ---


def test_convert_document_rejects_unsupported_suffix(tmp_path):
    path = tmp_path / "manual.doc"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        convert_document(path)


def test_supported_suffixes_are_expected_formats():
    assert SUPPORTED_SUFFIXES == {".pdf", ".docx", ".txt", ".md"}


# --- 구조 청킹 (AI_RAG01_CHUNK01) ---


def test_chunk_document_rejects_nonpositive_max_tokens(tmp_path):
    path = tmp_path / "manual.md"
    path.write_text("# 제목" + NL * 2 + "본문", encoding="utf-8")
    with pytest.raises(ValueError):
        chunk_document(convert_document(path), max_tokens=0)


def test_chunk_document_prefixes_heading_context(tmp_path):
    # HybridChunker.contextualize는 소속 섹션 heading을 청크 본문 앞에 붙임
    path = tmp_path / "manual.docx"
    _write_docx(path, [("heading", "알람 대응 절차"), ("paragraph", "챔버 압력을 확인합니다")])
    chunks = chunk_document(convert_document(path), max_tokens=MAX_TOKENS)
    assert chunks
    assert any("알람 대응 절차" in chunk and "챔버 압력을 확인합니다" in chunk for chunk in chunks)


def test_chunk_document_keeps_short_section_in_single_chunk(tmp_path):
    # 토큰 상한은 고정 크기가 아니라 초과 시 재분할하는 상한, 짧은 문서는 분할되지 않음
    path = tmp_path / "manual.md"
    path.write_text("# 점검" + NL * 2 + "센서 값을 기록합니다", encoding="utf-8")
    assert len(chunk_document(convert_document(path), max_tokens=MAX_TOKENS)) == 1


def test_chunk_document_splits_when_section_exceeds_max_tokens(tmp_path):
    # 상한을 넘는 섹션은 재분할, 상한이 실제로 적용되는지 확인
    path = tmp_path / "manual.docx"
    blocks = [("heading", "긴 절차")]
    blocks += [("paragraph", f"{index}번 단계를 수행하고 결과를 기록합니다") for index in range(60)]
    _write_docx(path, blocks)
    assert len(chunk_document(convert_document(path), max_tokens=32)) > 1


def test_chunk_document_normalizes_chunk_body(tmp_path):
    path = tmp_path / "manual.md"
    path.write_text("# 점검" + NL * 2 + "tem" + SOFT_HYPHEN + "perature 확인", encoding="utf-8")
    chunks = chunk_document(convert_document(path), max_tokens=MAX_TOKENS)
    assert any("temperature 확인" in chunk for chunk in chunks)
    assert all(SOFT_HYPHEN not in chunk for chunk in chunks)


def test_chunk_document_reads_txt_as_markdown(tmp_path):
    # Docling이 .txt 입력 형식을 지원하지 않아 마크다운으로 간주해 변환
    path = tmp_path / "manual.txt"
    path.write_text("센서 점검 절차", encoding="utf-8")
    chunks = chunk_document(convert_document(path), max_tokens=MAX_TOKENS)
    assert any("센서 점검 절차" in chunk for chunk in chunks)


# --- 적재 레코드 (AI_RAG01_CHUNK01) ---


def test_build_chunk_records_assigns_sequential_index_and_metadata():
    chunks = build_chunk_records(
        ["첫 청크", "둘째 청크", "셋째 청크"],
        doc_id="MAN-TST-001",
        equipment_type="Etching",
        alarm_code="ERR-402",
    )
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]
    assert all(c["doc_id"] == "MAN-TST-001" for c in chunks)
    assert all(c["equipment_type"] == "Etching" for c in chunks)
    assert all(c["alarm_code"] == "ERR-402" for c in chunks)
    assert all(c["content"] for c in chunks)
    assert all("embedding" not in c for c in chunks)


def test_build_chunk_records_defaults_metadata_to_none():
    chunks = build_chunk_records(["본문"], doc_id="MAN-TST-002")
    assert chunks[0]["equipment_type"] is None
    assert chunks[0]["alarm_code"] is None


def test_build_chunk_records_empty_returns_empty_list():
    assert build_chunk_records([], doc_id="MAN-TST-003") == []

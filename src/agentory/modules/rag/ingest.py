"""매뉴얼 인제스트 파이프라인 (AI_RAG01_PREP01)

파싱 → 정규화, 후속 단계(청킹·임베딩·적재)는 AI_RAG01_CHUNK01
PDF·DOCX는 Docling 변환으로 마크다운 추출, txt·md는 평문 디코드
Docling 선정 근거: 표 구조 보존·러닝 헤더/푸터 자동 제외·알람코드 무손실 (P1/P2 비교 실측)
"""

import re
import unicodedata
from pathlib import Path

# 지원 입력 형식, 그 외 확장자는 파라미터 오류
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})

# Docling 변환 대상 형식 (레이아웃·표 인식 필요)
_DOCLING_SUFFIXES = frozenset({".pdf", ".docx"})

# 제로폭 문자·소프트하이픈 삭제 매핑 (soft hyphen, ZWSP, ZWNJ, ZWJ, BOM)
_ZERO_WIDTH_DELETION = dict.fromkeys((0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF), None)
# 개행(\n)·탭(\t) 제외 C0/C1 제어문자
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# 줄바꿈으로 끊긴 라틴 단어, 양쪽이 라틴 글자일 때만 연결
_DEHYPHEN_RE = re.compile(r"([A-Za-z])-\n([A-Za-z])")

# Docling 변환기 모듈 캐시, 임포트·모델 로드가 무거워 최초 사용 시 1회 생성
_converter = None


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


def _parse_docling(path: Path) -> str:
    """Docling 변환 후 마크다운 추출, 표 구조 보존·러닝 헤더/푸터 자동 제외"""
    result = _get_converter().convert(str(path))
    return result.document.export_to_markdown()


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

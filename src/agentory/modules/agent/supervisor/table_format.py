"""Finalizer 답변의 마크다운 표 형식 정규화 (BE_CHAT01_STREAM01)

LLM이 표 구분자 행을 매번 다르게 생성(||-- vs |--|-- 등)하는 문제를 후처리로 고정
헤더 열 수 기준으로 구분자 행을 |:---|:---| 형태로 통일하고 셀 여백을 정돈
Finalizer는 토큰 스트리밍되므로, 완성된 줄 단위로 정규화하는 스트리밍 정규화기 제공
표 블록만 완결까지 잠깐 버퍼링하고 산문은 즉시 흘려 타이핑 UX를 유지
"""

# 정규화 후 구분자 행의 표준 셀(왼쪽 정렬)
_SEP_CELL = ":---"
_SEP_CHARS = set("|:- ")


def _is_table_row(body: str) -> bool:
    # 표 행 판정: 파이프로 시작하고 파이프가 2개 이상
    s = body.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_separator(body: str) -> bool:
    # 구분자 행 판정: 파이프·콜론·하이픈·공백만으로 구성되고 하이픈 1개 이상
    s = body.strip()
    return bool(s) and all(c in _SEP_CHARS for c in s) and "-" in s


def _cells(body: str) -> list[str]:
    # 양끝 파이프 제거 후 셀 분리(여백 정돈)
    return [c.strip() for c in body.strip().strip("|").split("|")]


def _fmt_row(body: str) -> str:
    # 표 행을 | a | b | 형태로 정규화
    return "| " + " | ".join(_cells(body)) + " |"


def _fmt_separator(cols: int) -> str:
    # 헤더 열 수만큼 표준 구분자 행 생성
    return "|" + "|".join([_SEP_CELL] * max(cols, 1)) + "|"


class StreamingTableNormalizer:
    """완성된 줄 단위로 표를 정규화하는 스트리밍 정규화기

    feed(delta)로 토큰을 넣고 지금 방출해도 안전한 텍스트를 즉시 반환
    표 후보 헤더는 다음 줄(구분자 여부)이 올 때까지 1줄만 잠깐 보류
    스트림 종료 시 flush()로 잔여 버퍼를 마저 방출
    """

    def __init__(self) -> None:
        self._buf = ""  # 아직 개행이 안 온 미완성 꼬리
        self._held: str | None = None  # 판정 대기 중인 표 후보 헤더 줄(개행 포함)
        self._in_table = False

    def feed(self, delta: str) -> str:
        self._buf += delta
        out: list[str] = []
        while True:
            idx = self._buf.find("\n")
            if idx == -1:
                break
            line = self._buf[: idx + 1]  # 개행 포함
            self._buf = self._buf[idx + 1 :]
            out.append(self._consume(line))
        return "".join(out)

    def flush(self) -> str:
        # 잔여 미완성 줄과 보류 헤더를 마저 방출
        out = ""
        if self._buf:
            out += self._consume(self._buf)  # 개행 없는 마지막 줄
            self._buf = ""
        if self._held is not None:
            out += self._held  # 구분자가 끝내 안 온 후보는 산문으로 확정
            self._held = None
        self._in_table = False
        return out

    def _consume(self, line: str) -> str:
        # 한 줄(개행 포함 가능)을 상태에 따라 정규화해 방출 텍스트 반환
        has_nl = line.endswith("\n")
        body = line[:-1] if has_nl else line
        nl = "\n" if has_nl else ""
        result = ""

        if self._in_table:
            if _is_table_row(body):
                return _fmt_row(body) + nl
            # 비표 줄이면 표 종료 후 아래 일반 처리로 진행
            self._in_table = False

        if self._held is not None:
            if _is_separator(body):
                # 헤더 + 구분자 확정: 헤더 열 수로 구분자 재생성
                held_body = self._held.rstrip("\n")
                held_nl = "\n" if self._held.endswith("\n") else ""
                cols = len(_cells(held_body))
                self._held = None
                self._in_table = True
                return _fmt_row(held_body) + held_nl + _fmt_separator(cols) + nl
            # 구분자가 아니면 보류 헤더는 산문으로 확정 후 현재 줄 계속 판정
            result += self._held
            self._held = None

        # 표 후보 헤더는 다음 줄 판정을 위해 1줄 보류(완성된 줄일 때만)
        if has_nl and _is_table_row(body) and not _is_separator(body):
            self._held = line
            return result
        return result + line


def normalize_markdown_tables(text: str) -> str:
    # 완성된 전체 텍스트를 한 번에 정규화(비스트리밍·테스트용)
    n = StreamingTableNormalizer()
    return n.feed(text) + n.flush()

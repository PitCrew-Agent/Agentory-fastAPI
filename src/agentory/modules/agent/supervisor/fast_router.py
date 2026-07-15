"""Fast Router: 규칙 우선 질의 분류 + Direct Answer 바이패스 (AI_AGENT01_REACT01, #139)

인사·감사·잡담 등 데이터 수집이 불필요한 질의를 규칙으로 즉시 판별해
Supervisor·워커를 거치지 않고 Finalizer로 직행, 불필요한 라우팅 LLM 호출·지연 제거
진단 계열 질의는 기존 Supervisor 경로로 위임
direct 오탐은 데이터 누락으로 직결되므로 고정밀 기준 적용, 애매하면 diagnostic 유지
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from agentory.modules.agent.supervisor.state import AgentState

DIRECT = "direct"
DIAGNOSTIC = "diagnostic"

# 진단·조회 신호, 하나라도 있으면 데이터 수집 경로 유지 (바이패스 금지)
_DIAGNOSTIC_SIGNAL = re.compile(
    r"(설비|장비|센서|알람|경보|온도|압력|진동|이상|불안정|징후|원인|조치|진단|상태|"
    r"로그|이력|정비|수리|고장|점검|매뉴얼|라인|EQP-|ERR-|WRN-|line)",
    re.IGNORECASE,
)
# 인사·감사·작별·잡담 신호 (명백한 경우만 direct 판정)
_SMALLTALK_SIGNAL = re.compile(
    r"(안녕|안뇽|하이|헬로|반가|반갑|고마워|고맙|감사|수고|잘\s*부탁|처음\s*뵙|"
    r"잘\s*있어|또\s*봐|^ㅎㅇ$|^hi$|^hello$|^hey$|^thanks$|^thank\s*you$|^bye$)",
    re.IGNORECASE,
)


def classify_intent(query: str) -> str:
    # 규칙 우선 분류, 진단 신호 우선 확인 후 잡담 신호 판정
    text = (query or "").strip()
    if not text:
        return DIAGNOSTIC
    if _DIAGNOSTIC_SIGNAL.search(text):
        return DIAGNOSTIC
    if _SMALLTALK_SIGNAL.search(text):
        return DIRECT
    return DIAGNOSTIC


def _last_user_text(messages: list) -> str:
    # 마지막 사용자(human) 발화 추출, 없으면 빈 문자열
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "human":
            return str(msg.content)
    return ""


def make_fast_router_node() -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def fast_router_node(state: AgentState) -> dict[str, Any]:
        # 마지막 사용자 발화 기준 분류, LLM 호출 없이 규칙만 적용
        intent = classify_intent(_last_user_text(state.get("messages", [])))
        return {"intent": intent}

    return fast_router_node

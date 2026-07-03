"""Supervisor 그래프 상태 정의 (AI_AGENT01_REACT01 / AI_AGENT02_CHAIN01)

설계 문서 docs/agent/architecture.md §3 기준
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    # 대화·추론 누적 (LangGraph reducer로 append)
    messages: Annotated[list, add_messages]
    # 컨텍스트 장부: Observation에서 추출한 핵심 엔티티 (AI_AGENT02_CHAIN01)
    entities: dict[str, Any]
    # 전역 반복 예산 (AI_AGENT03_FALLBACK01)
    step_count: int
    # Supervisor 라우팅 결과: 워커명 또는 FINISH
    next: str
    # Supervisor가 워커에 전달하는 구체 지시
    task: str
    # 라우팅 근거, SSE thought·도구 선택 근거 노출용 (NEW_TRUST03_REASON01)
    route_reason: str
    # 답변 근거 (doc_id·데이터 기준 시각) (NEW_TRUST01_CITE01)
    citations: list[dict[str, Any]]

"""Supervisor 그래프 상태 정의 (AI_AGENT01_REACT01 / AI_AGENT02_CHAIN01)

도구 결과에서 추출한 핵심 엔티티(equipment_id, alarm_code)를 상태에 유지,
다음 도구 호출 입력에 자동 연동 (FR-02)
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    # 도구 연쇄용 컨텍스트 (AI_AGENT02_CHAIN01)
    entities: dict[str, Any]  # 예: {"equipment_id": "EQP-003", "alarm_code": "ERR-402"}
    # 무한 루프 방지 (AI_AGENT03_FALLBACK01)
    step_count: int

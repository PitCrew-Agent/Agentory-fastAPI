"""Supervisor 노드: 구조화 라우팅 + 결정론 폴백 (설계 문서 §4.2)

매 턴 Route(next·reason·task)를 구조화 출력으로 산출
파싱 실패·예산 소진은 LLM 판단 없이 규칙으로 처리 (AI_AGENT03_FALLBACK01)
"""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from pydantic import BaseModel, Field

from agentory.core.config import get_settings
from agentory.modules.agent.prompts.supervisor import SUPERVISOR_SYSTEM_PROMPT
from agentory.modules.agent.supervisor.state import AgentState
from agentory.modules.agent.workers.registry import WORKERS

log = logging.getLogger(__name__)

FINISH = "FINISH"


class Route(BaseModel):
    # Supervisor의 턴별 결정, reason은 근거 노출(NEW_TRUST03_REASON01)에 재사용
    next: Literal["data_analysis", "knowledge", "maintenance", "FINISH"]
    reason: str = Field(description="이 선택을 한 근거")
    task: str = Field(default="", description="워커에게 전달할 구체 지시")


def _progress_summary(state: AgentState) -> str:
    # 조기 종료 판단 근거: 파악된 엔티티 + 이미 보고한 워커 집계
    reports: Counter = Counter()
    for msg in state["messages"]:
        # 도구 호출 없는 워커 AIMessage = 보고 완료
        is_report = isinstance(msg, AIMessage) and msg.content and not msg.tool_calls
        if is_report and msg.name in WORKERS:
            reports[msg.name] += 1
    entities = state.get("entities") or "없음"
    reported = (
        ", ".join(f"{name}({count}회)" for name, count in reports.items()) if reports else "없음"
    )
    return f"파악된 엔티티: {entities}\n보고 완료한 워커: {reported}"


def make_supervisor_node(llm: BaseChatModel) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    structured_llm = llm.with_structured_output(Route)

    async def supervisor_node(state: AgentState) -> dict[str, Any]:
        step_count = state.get("step_count", 0)
        max_steps = get_settings().agent_max_steps

        # 전역 폴백: 예산 소진 시 LLM 호출 없이 종료 강제 (AI_AGENT03_FALLBACK01)
        if step_count >= max_steps:
            log.warning("[supervisor] step 예산(%d) 소진, FINISH 강제", max_steps)
            return {
                "next": FINISH,
                "route_reason": f"반복 예산({max_steps}) 소진으로 수집 종료",
                "task": "",
                "step_count": step_count + 1,
            }

        # 수집 현황을 시스템 프롬프트에 주입해 조기 종료 판단 지원
        system = f"{SUPERVISOR_SYSTEM_PROMPT}\n\n[수집 현황]\n{_progress_summary(state)}"
        try:
            route = await structured_llm.ainvoke(
                [SystemMessage(content=system), *state["messages"]]
            )
        except Exception as exc:
            # 파싱·호출 실패 폴백: 첫 턴이면 데이터 수집부터, 이후엔 종료
            log.warning("[supervisor] 라우팅 실패, 결정론 폴백 적용: %s", exc)
            fallback = "data_analysis" if step_count == 0 else FINISH
            return {
                "next": fallback,
                "route_reason": "라우팅 실패로 기본 경로 적용",
                "task": "사용자 질의에 필요한 데이터 수집" if fallback != FINISH else "",
                "step_count": step_count + 1,
            }

        return {
            "next": route.next,
            "route_reason": route.reason,
            "task": route.task,
            "step_count": step_count + 1,
        }

    return supervisor_node

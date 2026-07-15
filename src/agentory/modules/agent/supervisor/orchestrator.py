"""하이브리드 오케스트레이터: 단일 ReAct 에이전트 + 병렬 Fetch (AI_AGENT01_REACT01, #139)

Supervisor 라우팅 + 워커별 ReAct 서브루프를 대체
Planner(모든 도구 bind)가 라운드마다 필요한 도구를 한꺼번에 emit
Fetch가 asyncio.gather로 병렬 실행, 관찰 후 Planner 재판단(ReAct)
라운드 예산은 config(agent_fetch_rounds_max)
도구 실행·반복 차단·관찰 절단·엔티티 추출은 워커와 동일 규율 유지 (AI_AGENT03_FALLBACK01)
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from agentory.common.events import AgentName
from agentory.core.config import get_settings
from agentory.modules.agent.context import extract_entities, format_entities, merge_entities
from agentory.modules.agent.prompts.orchestrator import PLANNER_SYSTEM_PROMPT
from agentory.modules.agent.supervisor.state import AgentState

log = logging.getLogger(__name__)

PLANNER = "planner"
FETCH = "fetch"
FINISH = "finish"

NodeFn = Callable[[AgentState], Awaitable[dict[str, Any]]]

# 도구가 속한 MCP 서버 → SSE AgentName, 단일 노드 통합에도 도메인 라벨 유지 (#139)
_AGENT_BY_SERVER = {
    "realtime": AgentName.DATA_ANALYSIS,
    "maintenance": AgentName.MAINTENANCE,
    "knowledge": AgentName.KNOWLEDGE,
}


def build_tool_agent_map(tools_by_server: dict[str, list[BaseTool]]) -> dict[str, AgentName]:
    # 도구명 → AgentName 매핑 구성, streaming이 action·observation의 agent 라벨을 채우는 데 사용
    return {
        tool.name: _AGENT_BY_SERVER[server]
        for server, tools in tools_by_server.items()
        for tool in tools
        if server in _AGENT_BY_SERVER
    }


def _truncate_observation(content: str) -> str:
    # 관찰값 프롬프트 재주입 상한, 대용량 결과가 다음 LLM 컨텍스트를 넘기지 않도록 절단
    limit = get_settings().agent_tool_observation_max_chars
    if len(content) <= limit:
        return content
    omitted = len(content) - limit
    return f"{content[:limit]}\n... (결과 과다로 {omitted}자 생략, 조회 범위를 좁혀 재시도 권장)"


def make_planner_node(llm: BaseChatModel, tools: list[BaseTool], rounds_max: int) -> NodeFn:
    # 모든 도구를 bind한 단일 에이전트, 라운드마다 필요한 도구를 스스로 선택
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    async def planner_node(state: AgentState) -> dict[str, Any]:
        step = state.get("step_count", 0)
        # 라운드 예산 소진: LLM 호출 없이 도구 미호출 메시지로 Finalizer행 유도 (FALLBACK01)
        if step >= rounds_max:
            log.warning("[planner] 라운드 예산(%d) 소진, 확보 근거로 종료", rounds_max)
            msg = AIMessage(content="수집 예산 소진, 확보된 근거로 답변")
            msg.name = PLANNER
            return {"messages": [msg], "step_count": step + 1}

        system = PLANNER_SYSTEM_PROMPT.format(
            now=datetime.now(UTC).isoformat(timespec="seconds"),
            entities=format_entities(state.get("entities", {})),
        )
        response = await llm_with_tools.ainvoke([SystemMessage(content=system), *state["messages"]])
        response.name = PLANNER
        return {"messages": [response], "step_count": step + 1}

    return planner_node


def make_fetch_node(tools: list[BaseTool]) -> NodeFn:
    # Planner가 emit한 도구 호출을 병렬 실행 (asyncio.gather)
    tools_by_name = {t.name: t for t in tools}

    async def _run_call(call: dict, history: set[str]) -> tuple[str, dict, str]:
        # 개별 도구 호출 실행, 반복 차단·실패 격리 적용 후 (시그니처, 호출, 관찰값) 반환
        sig = f"{call['name']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False)}"
        tool = tools_by_name.get(call["name"])
        if sig in history:
            content = "이미 동일 조건으로 조회함, 파라미터를 바꾸거나 다음 단계로 진행하라"
        elif tool is None:
            content = f"오류: 미등록 도구 {call['name']}"
        else:
            try:
                content = str(await tool.ainvoke(call["args"]))
            except Exception as exc:
                log.warning("[fetch] 도구 %s 실행 실패: %s", call["name"], exc)
                content = f"오류: 도구 실행 실패 ({exc})"
        return sig, call, _truncate_observation(content)

    async def fetch_node(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        history = set(state.get("tool_history", []))
        entities = dict(state.get("entities", {}))

        results = await asyncio.gather(*(_run_call(call, history) for call in calls))

        messages: list[ToolMessage] = []
        new_sigs: list[str] = []
        for sig, call, content in results:
            new_sigs.append(sig)
            entities = merge_entities(entities, extract_entities(content))
            messages.append(
                ToolMessage(content=content, tool_call_id=call["id"], name=call["name"])
            )
        return {"messages": messages, "entities": entities, "tool_history": new_sigs}

    return fetch_node


def route_after_planner(state: AgentState) -> str:
    # 도구 호출이 있으면 Fetch로(병렬 실행), 없으면 수집 종료로 판단해 Finalizer행
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return FETCH
    return FINISH

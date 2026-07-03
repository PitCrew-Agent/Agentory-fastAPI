"""워커 팩토리: ReAct 루프 직접 구현 (AI_AGENT01_REACT01)

prebuilt(create_react_agent) 미사용, agent 노드와 tool 노드의 조건부 왕복으로
Thought → Action → Observation 루프를 구성 (설계 문서 §4.1)
ReAct 로직은 이 파일 한 곳에만 존재, 워커는 registry에 스펙 등록만으로 추가
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from agentory.modules.agent.context import extract_entities, merge_entities
from agentory.modules.agent.supervisor.state import AgentState

log = logging.getLogger(__name__)

NodeFn = Callable[[AgentState], Awaitable[dict[str, Any]]]

WORKER_PROMPT_TEMPLATE = """{system_prompt}

[현재 시각(UTC)]
{now}

[현재 파악된 컨텍스트]
{entities}

[Supervisor 지시]
{task}

지시 수행에 필요한 도구를 호출하고, 수집이 끝나면 결과를 요약해 보고하라.
사용자에게 질문하지 마라, 보고는 Supervisor가 받아 다음 단계를 결정한다."""


def build_react_worker(
    name: str,
    llm: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
) -> tuple[NodeFn, NodeFn, Callable[[AgentState], str]]:
    # 반환: (agent 노드, tool 노드, agent 이후 라우팅 함수)
    llm_with_tools = llm.bind_tools(tools) if tools else llm
    tools_by_name = {t.name: t for t in tools}

    async def agent_node(state: AgentState) -> dict[str, Any]:
        # Thought/Action 단계: 현재 시각·장부·지시 주입 후 LLM이 도구 호출 여부 판단
        from datetime import UTC, datetime

        from agentory.modules.agent.context import format_entities

        system = WORKER_PROMPT_TEMPLATE.format(
            system_prompt=system_prompt,
            now=datetime.now(UTC).isoformat(timespec="seconds"),
            entities=format_entities(state.get("entities", {})),
            task=state.get("task", ""),
        )
        response = await llm_with_tools.ainvoke([SystemMessage(content=system), *state["messages"]])
        # 워커 식별용 이름 부여 (SSE 변환 시 agent 필드로 사용)
        response.name = name
        return {"messages": [response], "step_count": state.get("step_count", 0) + 1}

    async def tool_node(state: AgentState) -> dict[str, Any]:
        # Observation 단계: 도구 실행 결과를 ToolMessage로 추가, 엔티티 추출·병합
        import json

        last = state["messages"][-1]
        results: list[ToolMessage] = []
        entities = dict(state.get("entities", {}))
        history = state.get("tool_history", [])
        new_sigs: list[str] = []
        for call in getattr(last, "tool_calls", []):
            sig = f"{call['name']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False)}"
            tool = tools_by_name.get(call["name"])
            if sig in history or sig in new_sigs:
                # 반복 차단(AI_AGENT03_FALLBACK01): 동일 호출은 실행하지 않고 피드백만 주입
                content = "이미 동일 조건으로 조회함, 파라미터를 바꾸거나 다음 단계로 진행하라"
            elif tool is None:
                content = f"오류: 미등록 도구 {call['name']}"
            else:
                try:
                    content = str(await tool.ainvoke(call["args"]))
                except Exception as exc:
                    # 도구 폴백(AI_AGENT03_FALLBACK01): 실패를 Observation으로 주입해 대안 유도
                    log.warning("[agent:%s] 도구 %s 실행 실패: %s", name, call["name"], exc)
                    content = f"오류: 도구 실행 실패 ({exc})"
            new_sigs.append(sig)
            entities = merge_entities(entities, extract_entities(content))
            results.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))
        return {"messages": results, "entities": entities, "tool_history": new_sigs}

    def route_after_agent(state: AgentState) -> str:
        # 도구 호출 요청이 있으면 tool 노드로, 없으면 보고 완료로 Supervisor 복귀
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "supervisor"

    return agent_node, tool_node, route_after_agent

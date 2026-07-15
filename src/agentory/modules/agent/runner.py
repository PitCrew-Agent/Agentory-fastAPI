"""Agent 그래프 실행 진입점 (AI_AGENT01_REACT01)

그래프는 MCP 도구 로드 비용이 있어 최초 1회만 빌드해 캐시
초기 상태 구성과 그래프 제공을 담당
"""

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from agentory.modules.agent.mcp_client.client import load_tools_by_server
from agentory.modules.agent.supervisor.graph import build_agent_graph
from agentory.modules.agent.supervisor.orchestrator import build_tool_agent_map

_graph = None
_tool_agent_map: dict = {}
_lock = asyncio.Lock()

RECURSION_LIMIT = 40  # 워커 ReAct 왕복까지 포함한 그래프 재귀 상한


async def get_graph():
    # 그래프 지연 빌드 후 캐시 (MCP 도구 로드 1회), 도구→agent 매핑도 함께 캐시
    global _graph, _tool_agent_map
    async with _lock:
        if _graph is None:
            tools_by_server = await load_tools_by_server()
            _tool_agent_map = build_tool_agent_map(tools_by_server)
            _graph = await build_agent_graph(tools_by_server=tools_by_server)
    return _graph


def get_tool_agent_map() -> dict:
    # 오케스트레이터 경로의 SSE agent 라벨링용 도구→AgentName 매핑, get_graph 이후 유효
    return _tool_agent_map


def initial_state(query: str, history: list, equipment_id: str | None = None) -> dict[str, Any]:
    # 이전 대화(history) 뒤에 이번 질의를 붙여 초기 상태 구성
    # 선택 설비가 있으면 컨텍스트 장부에 시드해 라우터·워커·추천이 인지 (NEW_TWIN01_CHATCTX01)
    entities: dict[str, Any] = {"equipment_ids": [equipment_id]} if equipment_id else {}
    return {
        "messages": [*history, HumanMessage(content=query)],
        "entities": entities,
        "step_count": 0,
        "tool_history": [],
        "intent": "",
        "next": "",
        "task": "",
        "route_reason": "",
        "citations": [],
        "grounded": None,
    }

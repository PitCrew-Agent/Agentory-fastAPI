"""Agent 그래프 실행 진입점 (AI_AGENT01_REACT01)

그래프는 MCP 도구 로드 비용이 있어 최초 1회만 빌드해 캐시
초기 상태 구성과 그래프 제공을 담당
"""

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from agentory.modules.agent.supervisor.graph import build_agent_graph

_graph = None
_lock = asyncio.Lock()

RECURSION_LIMIT = 40  # 워커 ReAct 왕복까지 포함한 그래프 재귀 상한


async def get_graph():
    # 그래프 지연 빌드 후 캐시 (MCP 도구 로드 1회)
    global _graph
    async with _lock:
        if _graph is None:
            _graph = await build_agent_graph()
    return _graph


def initial_state(query: str, history: list) -> dict[str, Any]:
    # 이전 대화(history) 뒤에 이번 질의를 붙여 초기 상태 구성
    return {
        "messages": [*history, HumanMessage(content=query)],
        "entities": {},
        "step_count": 0,
        "tool_history": [],
        "next": "",
        "task": "",
        "route_reason": "",
        "citations": [],
        "grounded": None,
    }

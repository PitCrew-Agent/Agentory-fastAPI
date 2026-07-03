"""Agent 그래프 조립 (AI_AGENT01_REACT01)

Supervisor 라우팅 + 워커별 ReAct 서브루프(agent↔tool)를 직접 구성
FINISH 경로에 Finalizer(답변 합성) → Grounding(자가 검증) 노드 삽입
상세 설계는 docs/agent/architecture.md 참조
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph

from agentory.core.config import get_settings
from agentory.modules.agent.llm.base import get_chat_model
from agentory.modules.agent.mcp_client.client import load_tools_by_server
from agentory.modules.agent.supervisor.finalizer import (
    make_finalizer_node,
    make_grounding_node,
)
from agentory.modules.agent.supervisor.router import FINISH, make_supervisor_node
from agentory.modules.agent.supervisor.state import AgentState
from agentory.modules.agent.workers.base import build_react_worker
from agentory.modules.agent.workers.registry import WORKERS

FINALIZER = "finalizer"
GROUNDING = "grounding"


async def build_agent_graph(
    router_llm: BaseChatModel | None = None,
    worker_llm: BaseChatModel | None = None,
    finalizer_llm: BaseChatModel | None = None,
    tools_by_server: dict[str, list[BaseTool]] | None = None,
    grounding_enabled: bool | None = None,
):
    # 인자 주입은 테스트용, 미지정 시 설정 기반 LLM과 MCP 도구 사용
    router_llm = router_llm or get_chat_model("router")
    worker_llm = worker_llm or get_chat_model("worker")
    finalizer_llm = finalizer_llm or get_chat_model("finalizer")
    if tools_by_server is None:
        tools_by_server = await load_tools_by_server()
    if grounding_enabled is None:
        grounding_enabled = get_settings().agent_grounding_enabled

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", make_supervisor_node(router_llm))
    graph.add_node(FINALIZER, make_finalizer_node(finalizer_llm))

    # 레지스트리의 워커마다 ReAct 노드쌍(agent·tool) 생성·배선
    for name, spec in WORKERS.items():
        agent_node, tool_node, route_fn = build_react_worker(
            name, worker_llm, tools_by_server.get(spec.server, []), spec.prompt
        )
        graph.add_node(name, agent_node)
        graph.add_node(f"{name}_tools", tool_node)
        # ReAct 루프: agent에서 도구 호출 요청 시 tool로, 아니면 supervisor 복귀
        graph.add_conditional_edges(
            name, route_fn, {"tools": f"{name}_tools", "supervisor": "supervisor"}
        )
        graph.add_edge(f"{name}_tools", name)

    # Supervisor 라우팅: 워커 위임 또는 종료(Finalizer)
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["next"],
        {**{name: name for name in WORKERS}, FINISH: FINALIZER},
    )

    # 종료 경로: Finalizer → (선택) Grounding → END
    if grounding_enabled:
        graph.add_node(GROUNDING, make_grounding_node(finalizer_llm))
        graph.add_edge(FINALIZER, GROUNDING)
        graph.add_edge(GROUNDING, END)
    else:
        graph.add_edge(FINALIZER, END)

    graph.set_entry_point("supervisor")
    return graph.compile()

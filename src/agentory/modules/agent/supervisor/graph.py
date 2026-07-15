"""Agent 그래프 조립 (AI_AGENT01_REACT01)

Supervisor 라우팅 + 워커별 ReAct 서브루프(agent↔tool)를 직접 구성
FINISH 경로에 Finalizer(답변 합성) → Grounding(자가 검증) → Suggest(후속 추천) 노드 삽입
상세 설계는 docs/agent/architecture.md 참조
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph

from agentory.core.config import get_settings
from agentory.modules.agent.llm.base import get_chat_model
from agentory.modules.agent.mcp_client.client import load_tools_by_server
from agentory.modules.agent.supervisor.fast_router import (
    DIAGNOSTIC,
    DIRECT,
    make_fast_router_node,
)
from agentory.modules.agent.supervisor.finalizer import (
    make_finalizer_node,
    make_grounding_node,
)
from agentory.modules.agent.supervisor.orchestrator import (
    FETCH,
    PLANNER,
    make_fetch_node,
    make_planner_node,
    route_after_planner,
)
from agentory.modules.agent.supervisor.orchestrator import FINISH as FETCH_FINISH
from agentory.modules.agent.supervisor.router import FINISH, make_supervisor_node
from agentory.modules.agent.supervisor.state import AgentState
from agentory.modules.agent.supervisor.suggest import make_suggest_node
from agentory.modules.agent.workers.base import build_react_worker
from agentory.modules.agent.workers.registry import WORKERS

FAST_ROUTER = "fast_router"
FINALIZER = "finalizer"
GROUNDING = "grounding"
SUGGEST = "suggest"


async def build_agent_graph(
    router_llm: BaseChatModel | None = None,
    worker_llm: BaseChatModel | None = None,
    finalizer_llm: BaseChatModel | None = None,
    suggest_llm: BaseChatModel | None = None,
    planner_llm: BaseChatModel | None = None,
    tools_by_server: dict[str, list[BaseTool]] | None = None,
    grounding_enabled: bool | None = None,
    suggestions_enabled: bool | None = None,
    fast_router_enabled: bool | None = None,
    orchestrator_enabled: bool | None = None,
):
    # 인자 주입은 테스트용, 미지정 시 설정 기반 LLM 사용
    # LLM은 실제 쓰는 경로에서만 지연 생성, 미사용 역할의 불필요한 클라이언트 생성·키 요구 방지
    finalizer_llm = finalizer_llm or get_chat_model("finalizer")
    if tools_by_server is None:
        tools_by_server = await load_tools_by_server()
    if grounding_enabled is None:
        grounding_enabled = get_settings().agent_grounding_enabled
    if suggestions_enabled is None:
        suggestions_enabled = get_settings().agent_suggestions_enabled
    if fast_router_enabled is None:
        fast_router_enabled = get_settings().agent_fast_router_enabled
    if orchestrator_enabled is None:
        orchestrator_enabled = get_settings().agent_orchestrator_enabled

    graph = StateGraph(AgentState)
    graph.add_node(FINALIZER, make_finalizer_node(finalizer_llm))

    # 진단 경로: 오케스트레이터(단일 ReAct + 병렬 Fetch) 또는 Supervisor+워커 (#139)
    if orchestrator_enabled:
        planner_llm = planner_llm or get_chat_model("worker")
        all_tools = [t for tools in tools_by_server.values() for t in tools]
        rounds_max = get_settings().agent_fetch_rounds_max
        graph.add_node(PLANNER, make_planner_node(planner_llm, all_tools, rounds_max))
        graph.add_node(FETCH, make_fetch_node(all_tools))
        # ReAct 루프: 도구 호출 있으면 Fetch(병렬)로, 없으면 수집 종료(Finalizer)
        graph.add_conditional_edges(
            PLANNER, route_after_planner, {FETCH: FETCH, FETCH_FINISH: FINALIZER}
        )
        graph.add_edge(FETCH, PLANNER)
        diagnostic_entry = PLANNER
    else:
        router_llm = router_llm or get_chat_model("router")
        worker_llm = worker_llm or get_chat_model("worker")
        graph.add_node("supervisor", make_supervisor_node(router_llm))
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
        diagnostic_entry = "supervisor"

    # 종료 경로: Finalizer → (선택) Grounding → (선택) Suggest → END, 마지막 노드에서만 END 연결
    last = FINALIZER
    if grounding_enabled:
        graph.add_node(GROUNDING, make_grounding_node(finalizer_llm))
        graph.add_edge(last, GROUNDING)
        last = GROUNDING
    if suggestions_enabled:
        # 후속 추천은 경량 판단이라 router 모델 재사용
        suggest_llm = suggest_llm or get_chat_model("router")
        graph.add_node(SUGGEST, make_suggest_node(suggest_llm))
        graph.add_edge(last, SUGGEST)
        last = SUGGEST
    graph.add_edge(last, END)

    # Fast Router: 규칙 우선 분류로 잡담·범위 밖은 Finalizer 직행, 진단은 진단 경로 위임 (#139)
    if fast_router_enabled:
        graph.add_node(FAST_ROUTER, make_fast_router_node())
        graph.add_conditional_edges(
            FAST_ROUTER,
            lambda state: state.get("intent", DIAGNOSTIC),
            {DIRECT: FINALIZER, DIAGNOSTIC: diagnostic_entry},
        )
        graph.set_entry_point(FAST_ROUTER)
    else:
        graph.set_entry_point(diagnostic_entry)
    return graph.compile()

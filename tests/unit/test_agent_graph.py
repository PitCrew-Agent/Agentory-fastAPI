"""Agent 그래프 단위 테스트 (AI_AGENT01_REACT01)

FakeLLM으로 API 키 없이 라우팅·ReAct 루프·폴백을 결정론 검증 (설계 문서 §7)
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from agentory.modules.agent.context import extract_entities, merge_entities
from agentory.modules.agent.supervisor.graph import build_agent_graph
from agentory.modules.agent.supervisor.router import Route


class FakeRouterLLM:
    # with_structured_output 후 스크립트된 Route(또는 예외)를 순서대로 반환
    def __init__(self, script):
        self._script = iter(script)

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        item = next(self._script)
        if isinstance(item, Exception):
            raise item
        return item


class FakeWorkerLLM:
    # bind_tools 후 스크립트된 AIMessage를 순서대로 반환
    def __init__(self, script):
        self._script = iter(script)

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return next(self._script)


class FakeFinalizerLLM:
    # 최종 답변 합성·grounding 판정 겸용, 호출마다 고정 AIMessage 반환으로 실 API 호출 제거
    def __init__(self, content="최종 답변"):
        self._content = content

    async def ainvoke(self, messages):
        return AIMessage(content=self._content)


@tool
def get_sensor_logs(line_name: str) -> str:
    """테스트용 센서 로그 조회 도구"""
    return "EQP-003 temperature 65.0 alarm ERR-402"


@tool
def get_repair_history(equipment_id: str) -> str:
    """테스트용 수리 이력 조회 도구"""
    return "EQP-A05 repair 2026-07-02 ERR-401 냉각수 라인 세정"


def initial_state(step_count: int = 0) -> dict:
    return {
        "messages": [HumanMessage(content="B라인 이상 설비 확인")],
        "entities": {},
        "step_count": step_count,
        "next": "",
        "task": "",
        "route_reason": "",
        "citations": [],
    }


async def _build(router_script, worker_script):
    # 테스트 주입용 그래프 빌드 헬퍼
    return await build_agent_graph(
        router_llm=FakeRouterLLM(router_script),
        worker_llm=FakeWorkerLLM(worker_script),
        finalizer_llm=FakeFinalizerLLM(),
        # suggest 노드는 비활성이나 그래프 빌드 시 LLM이 선생성되므로 실 API 차단 위해 주입
        suggest_llm=FakeFinalizerLLM(),
        tools_by_server={
            "realtime": [get_sensor_logs],
            "knowledge": [],
            "maintenance": [get_repair_history],
        },
        suggestions_enabled=False,
        # 레거시 Supervisor 경로 검증용이므로 기본값 전환(#139)과 무관하게 명시적 off 고정
        orchestrator_enabled=False,
    )


async def test_react_loop_collects_observation_then_finishes():
    # 라우팅: data_analysis 위임 후 종료
    router = [
        Route(next="data_analysis", reason="센서 로그 필요", task="B라인 조회"),
        Route(next="FINISH", reason="수집 완료"),
    ]
    # 워커: 도구 호출 1회(Action) 후 보고(도구 호출 없음)
    worker = [
        AIMessage(
            content="",
            tool_calls=[{"name": "get_sensor_logs", "args": {"line_name": "B라인"}, "id": "c1"}],
        ),
        AIMessage(content="EQP-003에서 온도 65도, ERR-402 확인"),
    ]
    graph = await _build(router, worker)
    result = await graph.ainvoke(initial_state())

    # Observation(ToolMessage)이 히스토리에 남고 종료 상태 확인
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "EQP-003" in tool_messages[0].content
    assert result["next"] == "FINISH"
    # 컨텍스트 장부에 엔티티 자동 적재 (AI_AGENT02_CHAIN01)
    assert result["entities"]["equipment_ids"] == ["EQP-003"]
    assert result["entities"]["alarm_codes"] == ["ERR-402"]


async def test_step_budget_forces_finish_without_llm():
    # 예산 소진 상태면 라우터 LLM 호출 없이 FINISH 강제 (AI_AGENT03_FALLBACK01)
    graph = await _build(router_script=[], worker_script=[])
    result = await graph.ainvoke(initial_state(step_count=10))
    assert result["next"] == "FINISH"
    assert "예산" in result["route_reason"]


async def test_router_failure_falls_back_deterministically():
    # 첫 턴 라우팅 실패는 data_analysis 폴백, 이후 실패는 FINISH 폴백
    router = [RuntimeError("파싱 실패"), RuntimeError("파싱 실패")]
    worker = [AIMessage(content="조회할 데이터 없음 보고")]
    graph = await _build(router, worker)
    result = await graph.ainvoke(initial_state())
    assert result["next"] == "FINISH"
    assert "폴백" in result["route_reason"] or "실패" in result["route_reason"]


async def test_maintenance_worker_registered_in_graph():
    # maintenance 워커가 레지스트리·그래프에 배선되는지 확인 (BE_MCP05_MAINT01)
    from agentory.modules.agent.workers.registry import WORKERS

    assert "maintenance" in WORKERS
    assert WORKERS["maintenance"].server == "maintenance"
    graph = await _build(router_script=[], worker_script=[])
    nodes = graph.get_graph().nodes
    assert "maintenance" in nodes
    assert "maintenance_tools" in nodes


async def test_router_delegates_to_maintenance_and_collects_repair_history():
    # 라우팅→maintenance 워커→도구 호출→보고→종료 전체 경로 결정론 검증 (BE_MCP05_MAINT01)
    router = [
        Route(next="maintenance", reason="과거 수리 이력 필요", task="EQP-A05 수리 이력"),
        Route(next="FINISH", reason="수집 완료"),
    ]
    # 워커: get_repair_history 호출 1회(Action) 후 보고(도구 호출 없음)
    worker = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_repair_history", "args": {"equipment_id": "EQP-A05"}, "id": "m1"}
            ],
        ),
        AIMessage(content="EQP-A05 과거 ERR-401 냉각수 라인 세정 이력 확인"),
    ]
    graph = await _build(router, worker)
    result = await graph.ainvoke(initial_state())

    # Observation(ToolMessage)에 수리 이력이 담기고 종료
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "ERR-401" in tool_messages[0].content
    assert result["next"] == "FINISH"


async def test_fast_router_direct_answer_bypasses_supervisor():
    # 인사 질의는 Fast Router가 direct로 분류해 Supervisor·워커를 건너뛰고 Finalizer 직행 (#139)
    # 라우터 스크립트를 비워, Supervisor가 호출되면 route_reason에 폴백 흔적이 남도록 유도
    graph = await _build(router_script=[], worker_script=[])
    state = initial_state()
    state["messages"] = [HumanMessage(content="안녕하세요 반갑습니다")]
    result = await graph.ainvoke(state)

    assert result["intent"] == "direct"
    # Supervisor 미경유: route_reason이 초기값 그대로 비어 있음
    assert result.get("route_reason", "") == ""
    # 도구 관찰(ToolMessage) 없이 최종 답변만 생성
    assert not [m for m in result["messages"] if isinstance(m, ToolMessage)]


async def test_fast_router_routes_diagnostic_query_to_supervisor():
    # 진단 신호가 있는 질의는 diagnostic으로 분류해 기존 Supervisor 경로 유지 (#139)
    router = [Route(next="FINISH", reason="수집 완료")]
    graph = await _build(router, worker_script=[])
    result = await graph.ainvoke(initial_state())
    assert result["intent"] == "diagnostic"
    assert result["next"] == "FINISH"


async def _build_orchestrator(planner_script):
    # 오케스트레이터(단일 ReAct + 병렬 Fetch) 경로 그래프 빌드 헬퍼 (#139)
    return await build_agent_graph(
        planner_llm=FakeWorkerLLM(planner_script),
        finalizer_llm=FakeFinalizerLLM(),
        suggest_llm=FakeFinalizerLLM(),
        tools_by_server={
            "realtime": [get_sensor_logs],
            "knowledge": [],
            "maintenance": [get_repair_history],
        },
        suggestions_enabled=False,
        orchestrator_enabled=True,
    )


async def test_orchestrator_parallel_fetch_then_finish():
    # Planner가 한 라운드에 두 도구를 emit → Fetch가 병렬 실행 → 다음 라운드 종료 (#139)
    planner = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_sensor_logs", "args": {"line_name": "B라인"}, "id": "c1"},
                {"name": "get_repair_history", "args": {"equipment_id": "EQP-A05"}, "id": "c2"},
            ],
        ),
        AIMessage(content="수집 완료 보고"),
    ]
    graph = await _build_orchestrator(planner)
    result = await graph.ainvoke(initial_state())

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 2
    # 병렬 두 도구 결과가 모두 컨텍스트 장부에 적재 (AI_AGENT02_CHAIN01)
    assert "EQP-003" in result["entities"]["equipment_ids"]
    assert "EQP-A05" in result["entities"]["equipment_ids"]
    assert "ERR-402" in result["entities"]["alarm_codes"]


async def test_orchestrator_round_budget_forces_finish():
    # 라운드 예산(기본 3) 소진 시 Planner LLM 호출 없이 종료 강제 (AI_AGENT03_FALLBACK01, #139)
    def emit(i: int) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{"name": "get_sensor_logs", "args": {"line_name": f"L{i}"}, "id": f"c{i}"}],
        )

    # 3라운드 모두 도구 emit, 4번째는 예산 소진으로 LLM 미호출(스크립트 소비 안 됨)
    graph = await _build_orchestrator([emit(0), emit(1), emit(2)])
    result = await graph.ainvoke(initial_state())

    assert result["step_count"] >= 3
    # 예산 소진 후에도 Finalizer가 실행되어 최종 답변 생성
    assert any(isinstance(m, AIMessage) and m.content == "최종 답변" for m in result["messages"])


def test_classify_intent_rules():
    # 규칙 우선 분류: 잡담은 direct, 진단 신호가 섞이면 diagnostic 유지 (#139)
    from agentory.modules.agent.supervisor.fast_router import classify_intent

    assert classify_intent("안녕하세요") == "direct"
    assert classify_intent("고마워요 수고하세요") == "direct"
    # 진단 신호(설비·이상)가 있으면 인사말이 섞여도 데이터 수집 경로 유지
    assert classify_intent("안녕 B라인 이상 설비 알려줘") == "diagnostic"
    assert classify_intent("EQP-003 온도 상태 확인해줘") == "diagnostic"
    assert classify_intent("") == "diagnostic"


def test_extract_and_merge_entities():
    # 도구 결과 텍스트에서 설비·알람 추출 및 장부 병합 (ERR·WRN 알람 모두 인식)
    found = extract_entities("EQP-003 ERR-402 WRN-702 EQP-001")
    assert found == {
        "equipment_ids": ["EQP-001", "EQP-003"],
        "alarm_codes": ["ERR-402", "WRN-702"],
    }
    merged = merge_entities({"equipment_ids": ["EQP-002"]}, found)
    assert merged["equipment_ids"] == ["EQP-001", "EQP-002", "EQP-003"]

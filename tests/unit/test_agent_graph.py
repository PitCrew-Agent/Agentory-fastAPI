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
        tools_by_server={"realtime": [get_sensor_logs], "knowledge": []},
        suggestions_enabled=False,
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
            tool_calls=[{"name": "get_sensor_logs", "args": {"line_name": "B-Line"}, "id": "c1"}],
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


def test_extract_and_merge_entities():
    # 도구 결과 텍스트에서 설비·알람 추출 및 장부 병합 (ERR·WRN 알람 모두 인식)
    found = extract_entities("EQP-003 ERR-402 WRN-702 EQP-001")
    assert found == {
        "equipment_ids": ["EQP-001", "EQP-003"],
        "alarm_codes": ["ERR-402", "WRN-702"],
    }
    merged = merge_entities({"equipment_ids": ["EQP-002"]}, found)
    assert merged["equipment_ids"] == ["EQP-001", "EQP-002", "EQP-003"]

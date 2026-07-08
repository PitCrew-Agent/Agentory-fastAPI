"""골든 시나리오 E2E (TEST_HARNESS)

golden/*.yaml 케이스를 로드해 시나리오 주입→질의→응답을 속성 기반으로 검증
실 LLM 필요, `uv run pytest -m llm`으로만 실행
"""

import pytest
from langchain_core.messages import AIMessage

from tests.conftest import load_golden_cases

GOLDEN_CASES = load_golden_cases()

# knowledge MCP 소속 도구, 미배선 환경에서 해당 케이스만 skip 판단용 (BE_MCP04_RAG01)
KNOWLEDGE_TOOLS = {"search_manuals"}


def _called_tools(state: dict) -> set[str]:
    # 실행 중 호출된 도구명 집합 (AIMessage.tool_calls 수집)
    names: set[str] = set()
    for msg in state["messages"]:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                names.add(call["name"])
    return names


def _final_answer(state: dict) -> str:
    # 마지막 보고/답변 텍스트 (Finalizer 산출물)
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            return str(msg.content)
    return ""


@pytest.mark.llm
@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
async def test_golden_scenario(e2e_runner, case):
    run, ctx = e2e_runner
    expect = case.get("expect", {})

    # 기대 도구가 전부 knowledge 소속인데 미배선이면 이 케이스만 skip (나머지는 기존대로 부분 검증)
    expected_tools = set(expect.get("tools_called_any") or [])
    if expected_tools and expected_tools <= KNOWLEDGE_TOOLS and not ctx["knowledge_available"]:
        pytest.skip("knowledge 도구 미배선, 매뉴얼 검색 케이스 skip")

    result = await run(case["query"])

    # 도구 호출 검증: 기대 도구 중 하나 이상 호출
    if expect.get("tools_called_any"):
        called = _called_tools(result)
        assert set(expect["tools_called_any"]) & called, f"기대 도구 미호출, 실제={called}"

    # 답변 키워드 검증: 기대 키워드 중 하나 이상 포함
    answer = _final_answer(result)
    if expect.get("answer_contains_any"):
        assert any(k in answer for k in expect["answer_contains_any"]), (
            f"키워드 미포함: {answer[:200]}"
        )

    # 인용 검증은 지식(RAG) 도구가 있을 때만 활성, 미연동 시 이 검증만 건너뛰고 테스트는 통과
    # (전체 skip 대신 부분 검증, 김건 RAG 연동 후 자동 활성화)
    if expect.get("citations_include") and ctx["knowledge_available"]:
        doc_ids = {c.get("doc_id") for c in result.get("citations", [])}
        assert set(expect["citations_include"]) <= doc_ids, f"기대 인용 누락: {doc_ids}"

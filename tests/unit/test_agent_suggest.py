"""Suggest 노드 단위 테스트 (BE_CHAT02_SUGGEST01)

확인 범위 밖 설비·알람을 지어낸 추천을 하드 필터가 제거하는지 결정론 검증
"""

import pytest
from langchain_core.messages import HumanMessage

from agentory.modules.agent.supervisor.suggest import (
    Suggestions,
    _within_capability,
    _within_known_scope,
    make_suggest_node,
)


class FakeSuggestLLM:
    # with_structured_output 후 고정 Suggestions 반환으로 실 API 호출 제거
    def __init__(self, questions):
        self._questions = questions

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        return Suggestions(questions=list(self._questions))


def _state(entities):
    return {
        "messages": [HumanMessage(content="EQP-003 상태 확인")],
        "entities": entities,
    }


def test_within_known_scope_allows_known_and_id_free():
    known = {"EQP-003", "ERR-402"}
    # 확인된 ID 언급 또는 ID 미언급 질문은 통과(식별자 범위만 검사)
    assert _within_known_scope("EQP-003 추세 보여줘", known)
    assert _within_known_scope("담당 부서 확인해줘", known)


def test_within_known_scope_blocks_unknown_identifier():
    known = {"EQP-003"}
    # 확인 범위 밖 설비·알람은 차단
    assert not _within_known_scope("EQP-999 조회해줘", known)
    assert not _within_known_scope("ERR-500 원인 확인해줘", known)


def test_within_capability_allows_readonly_lookup():
    # 조회·확인·비교는 시스템 능력 안이라 통과
    assert _within_capability("이 설비 담당 부서·책임자 확인해줘")
    assert _within_capability("EQP-003 최근 알람 이력 확인해줘")
    assert _within_capability("관련 매뉴얼에서 원인·조치 방법 확인해줘")


def test_within_capability_blocks_external_actions():
    # 시스템이 못 하는 실행형 요청(연락·조치 요청·알림 발송 등)은 차단
    assert not _within_capability("담당(Main-Tech 1)에 조치 요청해줘")
    assert not _within_capability("담당자에게 연락해줘")
    assert not _within_capability("담당 부서에 알림 보내줘")
    assert not _within_capability("정비팀에 티켓 등록해줘")


@pytest.mark.asyncio
async def test_suggest_node_filters_fabricated_questions():
    llm = FakeSuggestLLM(
        [
            "EQP-003 조회 기간 늘려서 추세 보여줘",  # 확인된 설비, 통과
            "EQP-999 알람 이력 확인해줘",  # 확인 안된 설비, 제거
            "담당 부서에 조치 요청해줘",  # 능력 밖 실행 요청, 제거
            "이 설비 담당 부서 확인해줘",  # 담당 조회, 통과
        ]
    )
    node = make_suggest_node(llm)
    result = await node(_state({"equipment_ids": ["EQP-003"], "alarm_codes": ["ERR-402"]}))
    assert result["suggested_questions"] == [
        "EQP-003 조회 기간 늘려서 추세 보여줘",
        "이 설비 담당 부서 확인해줘",
    ]


@pytest.mark.asyncio
async def test_suggest_node_drops_external_action_requests():
    # 능력 밖 실행형 요청은 노드 후처리에서 전부 제거
    llm = FakeSuggestLLM(
        [
            "담당자에게 연락해줘",
            "정비팀에 조치 요청해줘",
            "EQP-003 최근 알람 이력 확인해줘",  # 유일하게 조회형, 통과
        ]
    )
    node = make_suggest_node(llm)
    result = await node(_state({"equipment_ids": ["EQP-003"]}))
    assert result["suggested_questions"] == ["EQP-003 최근 알람 이력 확인해줘"]


@pytest.mark.asyncio
async def test_suggest_node_drops_all_ids_when_no_context():
    # 확인된 컨텍스트가 없으면 특정 설비 지목 추천은 모두 제거
    llm = FakeSuggestLLM(["EQP-001 조회해줘", "공정 전체 알람 추이 보여줘"])
    node = make_suggest_node(llm)
    result = await node(_state({}))
    assert result["suggested_questions"] == ["공정 전체 알람 추이 보여줘"]


@pytest.mark.asyncio
async def test_suggest_node_isolates_llm_failure():
    class BoomLLM:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("boom")

    node = make_suggest_node(BoomLLM())
    result = await node(_state({"equipment_ids": ["EQP-003"]}))
    assert result["suggested_questions"] == []

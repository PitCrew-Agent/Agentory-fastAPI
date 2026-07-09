"""초기 상태 구성 단위 테스트 (NEW_TWIN01_CHATCTX01)

선택 설비를 컨텍스트 장부(entities)에 시드해 라우터·워커·추천이 인지하는지 검증
"""

from langchain_core.messages import HumanMessage

from agentory.modules.agent.runner import initial_state


def test_initial_state_seeds_selected_equipment():
    # 선택 설비가 있으면 entities에 equipment_ids로 시드
    state = initial_state("이 설비 상태 어때", [], equipment_id="EQP-A01")
    assert state["entities"] == {"equipment_ids": ["EQP-A01"]}


def test_initial_state_without_equipment_has_empty_entities():
    # 미지정 시 특정 설비 컨텍스트 없이 빈 장부 (기존 동작 유지)
    state = initial_state("전체 라인 상태 보여줘", [])
    assert state["entities"] == {}


def test_initial_state_appends_query_after_history():
    # history 뒤에 이번 질의를 붙여 멀티턴 순서 유지
    history = [HumanMessage(content="이전 질문")]
    state = initial_state("이번 질문", history, equipment_id="EQP-A01")
    assert [m.content for m in state["messages"]] == ["이전 질문", "이번 질문"]

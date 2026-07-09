"""장비 상태 기반 추천 메시지 생성 단위 테스트 (NEW_TWIN01_SUGGEST01)

확인 범위 밖 설비·알람을 지어낸 추천을 하드 필터가 제거하고, 생성 실패를 격리하는지 결정론 검증
"""

import pytest

from agentory.modules.agent.equipment_suggest import (
    EquipmentSuggestions,
    _within_known_scope,
    generate_equipment_suggestions,
)


class FakeSuggestLLM:
    # with_structured_output 후 고정 EquipmentSuggestions 반환으로 실 API 호출 제거
    def __init__(self, messages):
        self._messages = messages

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        return EquipmentSuggestions(messages=list(self._messages))


class BoomLLM:
    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        raise RuntimeError("boom")


def test_within_known_scope_allows_known_and_id_free():
    known = {"EQP-002", "ERR-401"}
    # 확인된 ID 언급 또는 ID 미언급 메시지는 통과
    assert _within_known_scope("EQP-002 온도 추세 보여줘", known)
    assert _within_known_scope("담당자에게 조치 요청해줘", known)


def test_within_known_scope_blocks_unknown_identifier():
    known = {"EQP-002"}
    # 확인 범위 밖 설비·알람은 차단
    assert not _within_known_scope("EQP-999 조회해줘", known)
    assert not _within_known_scope("ERR-500 원인 확인해줘", known)


@pytest.mark.asyncio
async def test_generate_filters_fabricated_messages():
    llm = FakeSuggestLLM(
        [
            "EQP-002 온도 추세 보여줘",  # 확인된 설비, 통과
            "EQP-999 알람 이력 확인해줘",  # 확인 안된 설비, 제거
            "담당자에게 조치 요청해줘",  # ID 미언급, 통과
        ]
    )
    result = await generate_equipment_suggestions(
        equipment_id="EQP-002",
        status="위험",
        alarm_code="ERR-401",
        alarm_metrics=["temperature"],
        sensors={"temperature": 305.0, "pressure": None},
        llm=llm,
    )
    assert result == ["EQP-002 온도 추세 보여줘", "담당자에게 조치 요청해줘"]


@pytest.mark.asyncio
async def test_generate_caps_at_three():
    llm = FakeSuggestLLM([f"EQP-002 항목{i} 보여줘" for i in range(5)])
    result = await generate_equipment_suggestions(equipment_id="EQP-002", status="양호", llm=llm)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_generate_blocks_alarm_when_status_normal():
    # 양호(알람 없음)면 알람 코드가 확인 범위에 없어 지어낸 알람 언급 메시지는 제거
    llm = FakeSuggestLLM(["EQP-002 최근 추세 보여줘", "ERR-401 원인 확인해줘"])
    result = await generate_equipment_suggestions(
        equipment_id="EQP-002", status="양호", alarm_code=None, llm=llm
    )
    assert result == ["EQP-002 최근 추세 보여줘"]


@pytest.mark.asyncio
async def test_generate_isolates_llm_failure():
    result = await generate_equipment_suggestions(
        equipment_id="EQP-002", status="주의", alarm_code="WRN-701", llm=BoomLLM()
    )
    assert result == []

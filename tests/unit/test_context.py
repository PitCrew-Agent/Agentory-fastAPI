"""컨텍스트 장부 엔티티 추출 단위 테스트 (AI_AGENT02_CHAIN01)

설비 ID 베이 형식(EQP-A01)과 숫자 형식(EQP-002)을 모두 추출하는지 검증
"""

from agentory.modules.agent.context import extract_entities


def test_extract_bay_style_equipment_ids():
    # 시뮬레이터 실제 설비 ID는 베이 형식, 정규식이 이를 놓치지 않아야 함
    found = extract_entities("EQP-A05 온도 상승, EQP-B04 가스유량 드리프트 ERR-402")
    assert found["equipment_ids"] == ["EQP-A05", "EQP-B04"]
    assert found["alarm_codes"] == ["ERR-402"]


def test_extract_mixed_numeric_and_bay_ids():
    # 숫자·베이 형식 혼재도 모두 추출, 중복 제거 후 정렬
    found = extract_entities("EQP-002 와 EQP-A01, EQP-002 재확인")
    assert found["equipment_ids"] == ["EQP-002", "EQP-A01"]


def test_extract_no_identifier_returns_empty():
    # 식별자 없는 텍스트는 빈 장부
    assert extract_entities("전체 라인 상태 알려줘") == {}

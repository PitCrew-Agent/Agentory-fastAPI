"""Knowledge 워커 프롬프트 회귀 테스트 (AI_AGENT01_PROMPT01)

knowledge.yaml 로드 결과와 필수 규칙 토큰 존재를 검증
"""

from agentory.modules.agent.prompts.knowledge import KNOWLEDGE_PROMPT, KNOWLEDGE_PROMPT_VERSION


def test_prompt_loaded_non_empty():
    assert KNOWLEDGE_PROMPT.strip()


def test_prompt_version_at_least_three():
    # version 1은 하드코딩 시절, 3부터 보고 상태 항목 포함
    assert KNOWLEDGE_PROMPT_VERSION >= 3


def test_prompt_has_required_sections():
    for section in ["[검색 판단]", "[질의 구성]", "[보고 형식]", "[금지 규칙]"]:
        assert section in KNOWLEDGE_PROMPT, f"필수 섹션 누락: {section}"


def test_prompt_report_has_five_elements():
    for token in ["상태", "검색 질의", "근거 요약", "부족 정보", "다음 검색 방향"]:
        assert token in KNOWLEDGE_PROMPT, f"보고 요소 누락: {token}"


def test_prompt_report_status_values():
    # Supervisor·Finalizer가 근거 충분/부족을 구분하는 고정 상태값
    assert "근거 충분" in KNOWLEDGE_PROMPT
    assert "근거 부족" in KNOWLEDGE_PROMPT


def test_prompt_citation_and_honesty_rules():
    # doc_id 인용 규칙과 무근거 정직 보고 규칙 (NEW_TRUST01_CITE01)
    assert "doc_id" in KNOWLEDGE_PROMPT
    assert "지어내지 않는다" in KNOWLEDGE_PROMPT
    # 빈 결과를 매뉴얼 부재로 단정하지 않는 표현
    assert "임계값 이상 근거 없음" in KNOWLEDGE_PROMPT
    assert "단정하지 않는다" in KNOWLEDGE_PROMPT

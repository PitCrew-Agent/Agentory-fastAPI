# 골든 질의 셋 포맷

E2E·RAG 품질 평가·LLM 응답 평가가 공유하는 테스트 케이스 정의 포맷.
파일 하나에 케이스 하나(또는 리스트)를 YAML로 작성한다.

```yaml
id: GOLDEN-001              # 고유 ID
title: 케이스 설명
seed_scenario: err402_temp_rise   # 시뮬레이터 시나리오 키 (simulator.ScenarioConfig.name)
query: "사용자 자연어 질의"
expect:
  tools_called_any: []      # 이 중 하나 이상 호출되어야 하는 도구명
  answer_contains_any: []   # 답변에 하나 이상 포함되어야 하는 키워드
  citations_include: []     # 인용에 포함되어야 하는 doc_id (NEW_TRUST01)
  rag_hit_docs: []          # RAG 검색 Top-K에 포함 기대 문서 (AI_RAG02_EVAL01)
```

- `expect` 하위 키는 검증 레이어별로 선택 사용한다. (E2E는 tools/answer, RAG 평가는 rag_hit_docs)
- 케이스는 각 검증 레이어 담당자가 추가한다.

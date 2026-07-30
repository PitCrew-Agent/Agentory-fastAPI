# 답변 품질 평가 셋 (AI_AGENT01_QUALITY01)

챗봇(대화형 에이전트) 답변 품질을 레거시(Supervisor+워커) vs 현재(Planner+병렬 Fetch) 구조로
정량 비교하기 위한 평가 셋입니다. 기존 `tests/golden`(회귀 게이트)과 분리해 이 디렉터리에 둡니다.

## 왜 분리했나

기존 품질 벤치는 규칙이 잘 처리하는 4개 질의만 채점해 두 구조 모두 `pass_rate 1.0`이 나왔습니다.
이는 절대 품질이 아니라 소규모 회귀 게이트 통과를 뜻합니다. 이 평가 셋은 규칙이 잘하는 질의에
더해 **적대적·함정 질의**(미존재 설비·폐지 코드·잘못된 전제·범위 밖·인젝션)를 대량 포함해
구조 간 실제 품질 차이가 드러나도록 설계합니다.

## 카테고리 (총 45건)

| 파일 | 카테고리 | 건수 | 목적 |
| --- | --- | --- | --- |
| eval_simple.yaml | simple | 10 | 단순 조회, 라우팅·엔티티 정확도 baseline |
| eval_diagnostic.yaml | diagnostic | 12 | 진단·멀티툴·매뉴얼 결합, 헤드라인 win-rate 주력 |
| eval_chitchat_oob.yaml | chitchat_oob | 8 | 잡담·범위 밖, 과잉 도구 호출 방지 |
| eval_adversarial.yaml | adversarial | 15 | 미존재·폐지·오전제·금지조치·인젝션 함정 |

## 케이스 스키마

```yaml
- id: EVAL-A01                     # 고유 ID
  category: simple                 # simple | diagnostic | chitchat_oob | adversarial
  title: 케이스 설명
  query: "사용자 자연어 질의"
  expect:
    tools_called_any: []           # 이 중 하나 이상 호출 기대 (라우팅 정확도)
    tools_called_all: []           # (선택) 반드시 모두 호출 기대
    tools_forbidden: []            # (선택) 호출되면 안 되는 도구
    no_tools: false                # (선택) 워커 도구 0회 기대 (잡담·인젝션)
    answer_contains_any: []        # 답변에 하나 이상 포함 기대 키워드
    answer_contains_all: []        # (선택) 모두 포함 기대
    citations_include: []          # 인용에 포함 기대 doc_id
    rag_hit_docs: []               # RAG 검색 Top-K 포함 기대 문서 (hit@k 평가)
    must_not_contain: []           # 등장 시 환각으로 감점할 문자열 (미존재 대상 한정)
    expect_honest_empty: false     # 빈 결과·미존재를 정직 보고해야 하는 케이스
  answer_gist: "이상적 답변 요지"    # LLM judge 루브릭·faithfulness 참고 (사람 가독)
```

- `rag_hit_docs`·`citations_include`는 실제 `search_manuals` 반환 기준으로 캘리브레이션합니다.
  적대적·정직 보고 케이스는 근거 문서가 없어 두 키를 비워 검색 평가 대상에서 제외합니다.
- `must_not_contain`은 미존재 설비·미존재 코드처럼 등장 자체가 환각인 경우에만 보수적으로 씁니다.
  오전제 정정처럼 정답에도 해당 문자열이 나올 수 있는 경우는 비우고 LLM 판정에 맡깁니다.

## 데이터 정답 근거 (시드 실측)

시드(`scripts/seed_data.py`, `simulator/scenarios.py::PRESETS["floor_demo"]`) 기준 활성 알람입니다.

| 설비 | 활성 알람 | 심각도 | 비고 |
| --- | --- | --- | --- |
| EQP-A05 | ERR-402 (온도) | 위험 | 복합 냉각 고장, 수리 이력 ERR-402 재발 |
| EQP-B03 | ERR-301 (압력) | 위험 | 압력 급성 이탈 |
| EQP-C02 | WRN-501 (가스) | 주의 | 총 가스 유량 편차 |
| EQP-C04 | WRN-801 (압력) | 주의 | 센서 변동성 증가 |
| EQP-C05 | ERR-201 (RF) + WRN-704 (가스) | 위험+주의 | RF 급성 + 가스 드리프트 |
| EQP-A03 | (규칙 알람 없음) | - | 밴드 안 이상, 챗봇 도구엔 정상으로 보임 |
| 그 외 14대 | 없음 | 양호 | A01·A02·A04·A06·A07·B01·B02·B04~B07·C01·C03·C06 |

라인: A라인(Main-Tech 1, 하경훈) / B라인(Main-Tech 2, 김철용) / C라인(Main-Tech 3, 이준호)
문서: MAN-SOP-001(대응 절차·승인·재가동) / MAN-GDL-001(상태·우선순위) / MAN-TM-8600(정의·원인·사양)

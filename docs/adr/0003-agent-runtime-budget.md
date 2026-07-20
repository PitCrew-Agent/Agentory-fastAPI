# ADR-0003: 에이전트 실행 예산·컨텍스트 방어 (가드레일)

- 상태: 승인 (2026-07-09), 일부 결정은 [ADR-0009](0009-agent-hybrid-orchestration.md) 전환으로 적용 범위 변경 (2026-07-20)
- 결정자: 주희정
- 관련: [ADR-0001](0001-architecture.md), [ADR-0009](0009-agent-hybrid-orchestration.md), [docs/agent/architecture.md](../agent/architecture.md), feature/52-context-overflow-fix(#53), feature/66-chatbot-perf(#67), feature/56-suggest-scope-filter(#57), feature/130-supervisor-finish-discipline(#131)

## 배경

에이전트 그래프 구조(Supervisor + 워커 ReAct)는 [docs/agent/architecture.md](../agent/architecture.md)에
정리되어 있습니다. 본 ADR은 그 구조를 실제 운영 가능한 품질·지연·비용으로 만들기 위해
구현 도중 추가한 **런타임 가드레일 결정**을 별도로 기록합니다. 이 결정들은 대부분 통합 이후
발견된 실패(컨텍스트 초과·응답 지연·근거 없는 추천)에 대한 대응으로 도입되었습니다.

### 적용 범위 (ADR-0009 전환 이후)

본 ADR이 전제한 Supervisor + 워커 ReAct는 [ADR-0009](0009-agent-hybrid-orchestration.md)에서
Planner + 병렬 Fetch로 교체되었고 기본 경로가 바뀌었습니다. 아래 결정들은 그 전환 이후에도
그대로 유효합니다.

- 저장소단·워커단 컨텍스트 상한(`sensor_log_max_rows`·`agent_tool_observation_max_chars`)
- 역할별 모델·reasoning_effort 이원화
- 전역 반복 예산(`agent_max_steps`), 오케스트레이터 경로에서는 Planner 라운드를 셉니다

반면 **Supervisor 종료 규칙(결정 3)과 워커 위임 예산 계산(결정 5)은 레거시 경로 전용**입니다.
오케스트레이터 경로에는 Supervisor 노드 자체가 없고, 라운드 예산은 `agent_fetch_rounds_max`가
담당합니다. 레거시 경로는 `agent_orchestrator_enabled=false`로 남아 있으므로 이 결정들을
폐기하지 않고 적용 범위만 한정해 보존합니다.

아래 "품질·속도 실측"(2026-07-14)은 레거시 경로 구성에서 측정한 값입니다. 두 경로를 같은 기준으로
비교한 최신 측정은 ADR-0009 3-2절에 있습니다.

## 결정

핵심 원칙은 컨텍스트 초과·과다 반복·품질 저하를 **LLM 판단에만 맡기지 않고 설정 상수 +
프롬프트 규칙 + 결정론적 후처리 3중으로 방어**한다는 것입니다.

### 1. 컨텍스트 초과 방지 (feature/52-context-overflow-fix #53)

센서 로그를 무제한 조회해 LLM 컨텍스트가 초과되는 실패가 있었습니다. 두 계층으로 막습니다.

- 저장소단: `fetch_sensor_logs`가 `sensor_log_max_rows`(기본 500) 행수 예산 안에서 조회합니다.
  요청 기간의 원시 tick이 예산에 들어가면 원시값을 그대로 반환하고, 예산을 넘으면 구간 집계로
  전환해 요청 기간 전체를 포괄합니다(BE_MCP02_TELEMETRY03). 집계 행은 구간 평균과 함께
  최소·최대, 구간 대표 알람을 실어 스파이크를 보존하며 `aggregated`·`bucket_seconds`로 표시됩니다.
  이전에는 최근 행 우선 절단이라 어떤 기간을 요청해도 마지막 약 41분만 반환되어, 응답이 요청
  기간 전체의 데이터인 것처럼 오인되는 문제가 있었습니다.
- 워커단 안전망: 관찰값이 `agent_tool_observation_max_chars`(기본 40000자)를 넘으면 절단하고
  안내 문구를 덧붙입니다.

### 2. 반복 예산·성능 튜닝 (feature/66-chatbot-perf #67)

응답 지연과 장황함을 줄이기 위해 예산과 모델 강도를 조정했습니다.

| 항목 | 변경 | 효과 |
| --- | --- | --- |
| `agent_max_steps` | 10 → 4 | 반복 상한 축소, 지연·비용 절감 |
| `agent_grounding_enabled` | True → False | 답변당 LLM 1회 절감 |
| reasoning_effort | worker=minimal, finalizer=low | 라우팅·워커 지연 단축 |
| `agent_history_max_messages` | 최근 12개만 로드 | 누적 히스토리 지연 방지 |
| Finalizer 프롬프트 | 6~8줄 압축·표 1개·서론 금지 | 답변 간결화 |
| Supervisor 규칙0 | 인사·잡담·범위밖 즉시 FINISH | 불필요한 워커 위임 차단 |

### 3. Supervisor 종료 규칙 명확화 (fix/13-supervisor-finish #14)

초기 종료 규칙이 모호해 워커 재위임·조회가 반복되었습니다. 매 턴 파악 엔티티·워커 보고 횟수를
집계한 `[수집 현황]`을 시스템 프롬프트에 주입하고, "찾지 못하면 재위임 금지", "완벽을 기다리지
말고 즉시 FINISH" 등 종료 규칙을 명문화했습니다.

### 4. 추천 질문 범위 필터 (feature/56-suggest-scope-filter #57)

추천 질문이 확인되지 않은 설비 ID·알람 코드를 지어내 답변 불가한 후속 질문을 노출했습니다.
프롬프트에 `[확인된 컨텍스트]`를 주입하고, 코드 후처리(`_within_known_scope`)로 확인 범위를
벗어난 식별자를 언급한 추천을 하드 차단합니다.

### 5. 워커 ReAct 턴을 예산에서 제외 (feature/130-supervisor-finish-discipline #131)

아래 "측정으로 드러난 한계"에서 후속 과제로 기록한 문제(`step_count`를 supervisor와 워커가 함께
증가시켜 워커 하나가 예산을 소진하는 문제)를 해소했습니다. 워커가 3종(data_analysis·knowledge·
maintenance)으로 늘면서 이 문제가 실제 진단을 잘라먹었기 때문입니다. 워커 agent 노드의 `step_count`
증가를 제거해 예산이 **Supervisor 위임 횟수만** 세도록 바로잡고, 3워커 경로가 잘리지 않도록
`agent_max_steps`를 상향했습니다. 워커 내부 루프는 도구 미호출 복귀·동일 호출 반복 차단·
`RECURSION_LIMIT`(40)로 이미 보호되므로 예산 공유가 불필요합니다.

| 항목 | 기존 | 현재 |
| --- | --- | --- |
| `step_count` 카운팅 | supervisor + 워커 ReAct 턴 공유 | Supervisor 위임 전용 |
| `agent_max_steps` | 4 | 6 |
| 3워커 심층 질의 step_count | 8 (워커 턴 포함) | 3 (위임 수) |
| 3워커 심층 질의 종료 | 예산 강제 FINISH, knowledge 단계 잘림 | 자연 FINISH, 3워커 완주 |
| 단순 질의 | 1스텝·워커 0개 | 동일 (규율 유지) |

라이브 실측(실 gpt-5-mini + MCP 3서버) 기준입니다. 아울러 Supervisor 프롬프트에 "필요한 워커만
최소 위임, 정보 모이면 즉시 FINISH" 규율을 강화해, 천장 상향이 습관적 전 워커 호출로 이어지지 않게
했습니다. 단 심층 질의 지연은 예산이 아니라 실제 작업량(워커별 ReAct + finalizer 순차 호출)에서
오므로, 완주는 보장되나 지연 절감은 조건부 grounding·꼬리 병렬화 등 별도 최적화 과제입니다.

### 답변 품질·속도 실측 (레거시 경로 구성, 2026-07-14)

워커 3종·`max_steps=6`·워커 턴 예산 제외 구성의 답변 품질과 속도를 실측했습니다. 하네스는
`scripts/bench/agent_quality_bench.py`(원자료 `agent_quality_result.json`)이며, 질의당 지연·LLM
호출수·steps와 품질을 집계합니다. 품질은 골든 방식의 객관 기준으로 판정합니다(라우팅: 기대 도구
호출 여부, 정답 키워드: 답변 내 기대 엔티티·코드 포함, 인용: knowledge 질의의 citations 유무).

- 측정 환경: 로컬, gpt-5-mini(전 역할), MCP 3서버 기동, DB 시드(EQP-A05=ERR-401+WRN-702·수리 2건),
  워커 3종 경로 + 범위밖 잡담 4종, 케이스당 2회(n=8), 2026-07-14

| 케이스 | 라우팅 | p50 지연 | LLM 호출 | steps | 품질 pass |
| --- | --- | --- | --- | --- | --- |
| data (진단) | data_analysis | 61.8s | 7.0 | 2.0 | 2/2 |
| knowledge (지식) | knowledge(±data) | 71.1s | 7.5 | 2.5 | 2/2 |
| maintenance (정비) | maintenance | 35.0s | 6.5 | 2.5 | 2/2 |
| chitchat (잡담) | 워커 미호출 | 3.6s | 2.0 | 1.0 | 2/2 |

- 품질: 전 케이스 pass, 라우팅 정확도 100%·정답 키워드 포함 100%·인용 100%입니다. 3종 워커가 각 질의를
  올바르게 담당하고, 잡담은 워커를 부르지 않고 즉시 종료합니다.
- 속도: 잡담 3.6s로 규율이 워커 0개를 유지하고, 심층 진단은 35~71s입니다. steps는 전 케이스 3 이하로
  예산 잘림 없이 자연 종료합니다.
- 지연의 성격: 심층 질의 지연은 작업량에서 옵니다. 예를 들어 진단 질의는 도구 3회(센서·알람·메타) +
  finalizer로 LLM을 7회 순차 호출합니다. 추가 절감은 조건부 grounding·꼬리 병렬화·워커 도구 배치 호출이
  후속 과제입니다.

## 정량 평가 (실측)

위 튜닝의 효과를 실측으로 확인했습니다. 측정 하네스는 `scripts/bench/agent_budget_bench.py`이며,
그래프를 in-process로 빌드해 질의당 지연·LLM 호출수·토큰을 콜백으로 집계합니다(원자료
`scripts/bench/agent_budget_result.json`).

- 측정 환경: 로컬, 모델 gpt-5-mini(전 역할), reasoning_effort router·worker=minimal·finalizer=low,
  DB 시드(설비 20·telemetry 15,520·knowledge 68), 2026-07-10
- 워크로드: 라우팅 경로가 다른 질의 3종(다단계 진단·지식 검색·단일 설비 진단), 설정당 각 3회(n=9, 총 27런)
- 지연 변동이 커 집계는 중앙값(median) 기준, suggestions는 교란 배제 위해 전 설정 off 고정

### 설정별 집계 (중앙값)

| 설정 | 지연 | LLM 호출 | 총토큰 | 비용(USD) | steps 범위 |
| --- | --- | --- | --- | --- | --- |
| A 이전 (steps=10, grounding on) | 76.8s | 9 | 164,942 | 0.052 | 4~9 |
| B (steps=4, grounding on) | 57.6s | 7 | 70,923 | 0.026 | 5~13 |
| C 이후 (steps=4, grounding off) | 38.5s | 6 | 79,690 | 0.026 | 4~8 |

비용은 gpt-5-mini 가정 단가(입력 0.25·출력 2.00 USD/1M)로 산출한 파생값이며, 토큰이 실측
지표입니다.

### 핵심 발견

- **before→after 종합(A→C)**: 지연 76.8→38.5s(−50%), 총토큰 164,942→79,690(−52%), 호출 9→6(−33%).
  방향은 중앙값 기준 확정적입니다.
- **`max_steps` 10→4(A→B, grounding 고정)**: 지연 76.8→57.6s(−25%), 총토큰 164,942→70,923(−57%)로
  토큰 절감이 가장 큽니다. 반복 상한 축소가 관찰값 누적 재주입을 줄이는 효과입니다.
- **grounding on→off(B→C)**: 질의당 grounding 노드 1회 제거로 호출이 7→6으로 줄고 지연도 감소하나,
  표본 변동이 커 지연 절감폭은 단정하기 어렵습니다.

### 측정으로 드러난 한계: max_steps가 워커 내부 루프를 제한하지 못함

`step_count`는 supervisor 노드와 워커 노드가 함께 증가시키는 공유 카운터이며
(`workers/base.py`), 예산 체크는 supervisor 진입 시에만 수행됩니다(`supervisor/router.py`).
따라서 한 번 워커에 위임되면 그 워커의 ReAct 내부 루프는 `max_steps`에 걸리지 않고
전역 `RECURSION_LIMIT`(40)까지 진행할 수 있습니다. 실측에서 이 경로가 재현되었습니다.

- max_steps=4(B)임에도 9런 중 1런이 13스텝·입력토큰 419,575·지연 79초로 폭주한 뒤 종료
- 그 결과 B의 총토큰 범위는 16,812~424,220, 지연은 최대 112초로 편차가 매우 큼

즉 `max_steps` 축소는 **평균·중앙 비용은 확실히 낮추지만 worst-case를 보장하지 못합니다.**
이 예산 공유 문제는 위 §5(feature/130 #131)에서 워커 턴의 `step_count` 증가를 제거해 해소했습니다.
다만 워커 내부 반복 자체의 별도 상한(워커별 tool 왕복 상한)은 여전히 미도입 상태로, `RECURSION_LIMIT`
(40)이 유일한 backstop입니다.

### 측정 한계

- n=9(설정당 3회)여도 gpt-5-mini 지연·토큰 변동이 큽니다. max_steps·before→after 방향은
  확정적이나 grounding 지연 효과는 표본 노이즈로 단정하기 어렵습니다.
- `agent_history_max_messages`(12) 효과는 본 하네스에서 미측정입니다(history 없이 실행).
  멀티턴 세션 누적 지연은 별도 워크로드로 측정해야 합니다.
- reasoning_effort의 라우터 지연 효과는 [docs/poc-verification.md](../poc-verification.md) §9의
  실측(default 8.91s → minimal 3.88s)을 함께 참조합니다.

## 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| 컨텍스트 초과를 프롬프트 지시로만 완화 | LLM이 지시를 무시하면 재발, 실패가 치명적 |
| Grounding 상시 on | 답변당 LLM 1회 추가, 지연·비용 부담 (설정으로 on 가능하게 유지) |
| 추천 필터를 프롬프트로만 | LLM이 식별자를 지어내면 통과, 신뢰성 우선해 코드 필터 병행 |
| `max_steps` 유지(10) | 데모 질의는 4단계 내 종료가 대부분, 지연이 더 큰 리스크 |

## 결과 / 미해결

- 컨텍스트·예산 상수는 모두 `core/config.py`에 모여 환경 변수로 조정 가능합니다.
- 세 계층 폴백(전역 예산·도구 재시도·동일 호출 반복 차단)으로 무한 루프가 구조적으로
  발생하지 않습니다(상세 [docs/agent/architecture.md](../agent/architecture.md) §4.4).
- 남은 리스크로 기록합니다.
  - `sensor_log_max_rows=500`의 최근 편향 절단은 구간 집계 전환으로 해소했습니다
    (BE_MCP02_TELEMETRY03). 다만 집계 구간에서는 개별 tick 파형이 평균으로 뭉개지므로,
    짧은 스파이크의 지속 시간·형태 분석은 여전히 좁은 기간 조회가 필요합니다.
  - `agent_history_max_messages=12`와 요청마다 초기화되는 엔티티 장부의 불일치로, 최근 12개
    밖 또는 assistant 답변에만 있던 설비 ID는 후속 턴 컨텍스트에서 누락될 수 있습니다.
  - Grounding 상시 off로 자가 검증 배지(NEW_TRUST02)가 미노출 상태이며, 성능과 신뢰의
    트레이드오프가 현재 성능 쪽으로 고정되어 있습니다.
  - 관찰값 40000자 상한은 실패 방지 backstop이지 경량화 수단은 아니며, 단계마다 재주입되면
    누적 컨텍스트가 여전히 큽니다.
  - (해결, §5) `step_count`를 supervisor·워커가 공유해 워커 하나가 예산을 소진하던 문제는
    feature/130(#131)에서 워커 턴의 증가를 제거해 예산을 위임 전용으로 바로잡았습니다. 3워커
    심층 질의 step_count가 8→3으로 줄고 knowledge 단계 잘림이 사라졌습니다. 단 워커 내부 반복
    자체의 상한(워커별 tool 왕복 상한)은 여전히 미도입이며 `RECURSION_LIMIT`(40)이 유일한 backstop입니다.

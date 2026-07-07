# Agentory PoC 검증 문서

## 1. 문서 정보

| 항목 | 내용 |
|---|---|
| 문서명 | Agentory 핵심 기술 요소 PoC 검증 문서 |
| 작성자 | 주희정 |
| 작성일 | 2026-07-07 |
| 버전 | v0.2 |
| 상태 | 검증 완료 (종합 Go, 조건부) |
| 관련 문서 | docs/adr/0001-architecture.md, docs/agent/architecture.md, docs/mcp/realtime.md |

### 변경 이력

| 버전 | 일자 | 작성자 | 변경 내용 |
|---|---|---|---|
| v0.1 | 2026-07-07 | 주희정 | 초안 작성, 검증 항목 3건 정의 |
| v0.2 | 2026-07-07 | 주희정 | POC-A/B/C 검증 실시, 결과·판정 기록 |

## 2. 배경 및 목적

Agentory는 자율형 제조 데이터 분석 및 MCP 에이전트 시스템으로, LangGraph 기반 Supervisor 구조와 MCP streamable-http 서버를 핵심으로 채택했습니다. 해당 아키텍처 결정(ADR 0001)은 문서상으로 확정되었으나, 실제 스택 위에서 성립하는지는 아직 코드로 검증되지 않았습니다.

본 프로젝트는 요구사항상 4주 일정을 2주로 압축하여 진행하므로, 본 구현 도중 근본적 방향 전환이 발생할 경우 일정 손실이 큽니다. 따라서 착수 초반에 최소 코드로 핵심 기술 요소의 동작 가능성을 증명하여 리스크를 사전에 해소하는 것이 본 PoC의 목적입니다.

## 3. 검증 가설

본 PoC는 다음 가설이 참임을 증명하는 것을 목표로 합니다.

- 가설 1: LangGraph로 직접 구성한 Supervisor 그래프가 입력에 따라 워커를 동적으로 라우팅할 수 있다.
- 가설 2: MCP python-sdk의 streamable-http로 realtime 서버를 기동하고, 클라이언트 tool 호출에 대해 SSE 이벤트 계약대로 응답할 수 있다.
- 가설 3: OpenAI 역할별 모델 선택(mini/nano) 구성이 정상 동작하며, 응답 지연과 비용이 가정한 범위에 든다.

## 4. 범위

### 검증 범위(In scope)

- Supervisor 그래프의 라우팅 분기 동작
- mcp-realtime 서버 기동 및 tool 1건 호출에 대한 SSE 응답
- OpenAI 역할별 모델의 호출 성공 여부와 지연·토큰·비용 측정

### 비범위(Out of scope)

- 라우팅 정확도 튜닝, 프롬프트 최적화
- 인증(Keycloak), RAG 검색 품질, 프론트엔드 연동
- 예외 처리, 재시도, 성능 최적화, 동시성
- 실제 제조 데이터 연동 및 시뮬레이터 정합성

## 5. 검증 환경

| 항목 | 내용 |
|---|---|
| 런타임 | Python (uv 관리) |
| 주요 라이브러리 | LangGraph, MCP python-sdk(streamable-http), langchain-openai |
| 위치 | scripts/poc/ (본 모듈과 격리, 검증 후 폐기) |
| 사전 조건 | .env의 OPENAI_API_KEY 설정, 로컬 실행 환경 구성 |
| SSE 계약 소스 | src/agentory/common/events.py |

## 6. 검증 항목

리스크가 큰 순서로 3개 항목을 선정합니다.

| ID | 검증 질문 | 대응 가설 | 담당 | 예상 소요 |
|---|---|---|---|---|
| POC-A | Supervisor가 입력에 따라 워커를 동적 라우팅하는가 | 가설 1 | 주희정 | 반나절 |
| POC-B | mcp-realtime가 tool 호출에 SSE로 응답하는가 | 가설 2 | 주희정 | 반나절 |
| POC-C | OpenAI 역할별 모델 호출·지연·비용이 예상 범위인가 | 가설 3 | 주희정 | 1~2시간 |

### 6.1 POC-A: Supervisor 동적 라우팅

- 검증 질문: LangGraph로 구성한 Supervisor 그래프가 서로 다른 입력에 대해 서로 다른 워커로 라우팅하는가
- 성공 기준
  - 성격이 다른 입력 2개 이상이 각각 다른 워커로 분기됨이 로그로 확인
  - 라우팅 결정 1건당 응답 5초 이내
  - 그래프가 예외 없이 종료 상태 도달
- 검증 절차
  1. 최소 노드(Supervisor 1, 워커 2)로 그래프 구성
  2. router=gpt-5-nano로 라우팅, 각 워커는 실행 여부만 로그 출력
  3. 입력 2종(데이터 조회성, 지식 검색성) 주입 후 분기 로그 관찰

### 6.2 POC-B: MCP realtime 연결 및 SSE 응답

- 검증 질문: mcp-realtime 서버가 streamable-http로 기동되고, 클라이언트 tool 1건 호출에 events.py 계약에 맞는 SSE 이벤트로 응답하는가
- 성공 기준
  - 서버 기동 및 클라이언트 세션 연결 성공
  - tool 1회 호출에 대해 결과가 SSE 이벤트로 수신
  - 수신 이벤트 형식이 events.py 계약과 일치
- 검증 절차
  1. tool 1개(고정값 반환)만 등록한 최소 mcp-realtime 서버 구성
  2. streamable-http 클라이언트로 세션 연결 후 tool 호출
  3. 수신 이벤트를 덤프하여 계약 필드와 대조

### 6.3 POC-C: OpenAI 역할별 모델 호출

- 검증 질문: 역할별로 지정한 OpenAI 모델이 정상 호출되고, 응답 지연과 토큰 사용량이 가정 범위에 드는가
- 성공 기준
  - worker=gpt-5-mini, router=gpt-5-nano 각각 응답 성공
  - 모델별 응답 지연·토큰 사용량 로그 확보
  - finalizer 후보(gpt-5.1 등) 비용·지연 비교 데이터 확보
- 검증 절차
  1. langchain-openai로 각 모델에 동일 프롬프트 1건 호출
  2. 응답 지연·입출력 토큰 수·개략 비용 측정
  3. finalizer 후보 모델을 함께 호출하여 비교

## 7. 일정

| 구분 | 시점 | 대상 |
|---|---|---|
| PoC 착수 | W1 1일차 | POC-A |
| 중간 | W1 2일차 | POC-B, POC-C |
| 판정 및 결론 | W1 3일차 | 종합 판정, 본 구현 반영 |

## 8. 위험 요소 및 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| LangGraph 직접 그래프 구성 난이도 | 라우팅 검증 지연 | 노드 최소화, 라우팅 로직만 우선 검증 |
| streamable-http 연동 실패 | MCP 전 기능 지연 | SSE 계약 최소 필드부터 단계 검증 |
| 모델 비용·지연이 가정 초과 | 역할별 모델 재선정 | finalizer 후보 비교 데이터로 대안 결정 |
| PoC 장기화 | 2주 일정 압박 | 항목별 성공 기준으로 조기 판정, 완성도 배제 |

## 9. 검증 결과

각 항목 검증 후 결과를 기록합니다. 검증은 2026-07-07 로컬 환경에서 실제 코드(`make_supervisor_node`, mcp streamable-http, langchain-openai)를 대상으로 수행했으며, 스파이크 코드는 `scripts/poc/`에 위치합니다.

| ID | 상태 | 판정 | 근거(로그·수치 요약) | 후속 조치 |
|---|---|---|---|---|
| POC-A | 완료 | 성공 | 입력 3종이 워커 2종(data_analysis, knowledge)으로 분기, 동적 라우팅 성립. 초기 지연 9.7~11.9초(성공 기준 5초 초과)는 조치 후 2.95~4.3초로 해소 | 조치 완료: 라우터 모델 gpt-5-nano→gpt-5-mini 변경, reasoning_effort=minimal 설정화(llm_router_reasoning_effort) |
| POC-B | 완료 | 성공 | 고정값 tool 서버 streamable-http 기동·세션 연결·tool 1회 호출·결과 수신 성공, events.py 계약(action/observation) 직렬화·역직렬화 통과 | 실서버 tool은 DB 의존이므로 통합 시 DB 연동 상태에서 재확인 |
| POC-C | 완료 | 성공 | router/worker/finalizer 후보 3개 모델 모두 응답 성공, gpt-5.1 모델명 유효 확인. 지연·토큰 측정 완료 | router용 gpt-5-nano가 출력 토큰 977개로 장황(지연 유발), 라우팅은 구조화 출력이라 실제 부담은 낮을 것으로 추정되나 통합 후 재측정 |

### POC-A 라우터 지연 조치 측정

라우터 gpt-5-mini에서 reasoning_effort 조정에 따른 지연 변화입니다(지식 검색 케이스 기준). 라우팅 결정은 모든 수준에서 동일하게 정확했습니다.

| reasoning_effort | 라우팅 결정 | 지연 |
|---|---|---|
| default | knowledge | 8.91초 |
| low | knowledge | 5.59초 |
| minimal | knowledge | 3.88초 |

minimal 채택 후 전체 케이스 재측정 결과 2.95~4.3초로 성공 기준(5초)을 충족합니다.

### POC-C 모델 측정 기록

동일 프롬프트("제조 라인 A의 온도 이상 여부를 한 문장으로 요약해줘", 입력 25토큰) 1회 호출 기준입니다.

| 역할 | 모델 | 응답 지연 | 입력 토큰 | 출력 토큰 | 비고 |
|---|---|---|---|---|---|
| router | gpt-5-nano | 8.21초 | 25 | 977 | reasoning 모델, 출력 장황 |
| worker | gpt-5-mini | 6.38초 | 25 | 398 | 균형형, 정상 응답 |
| finalizer 후보 | gpt-5.1 | 2.98초 | 25 | 31 | 최단 지연, 간결 응답 |

## 10. 결론 및 판정(Go / No-Go)

전체 검증 완료 후 아래를 확정합니다.

- 종합 판정: **Go**. 3개 핵심 요소(LangGraph 동적 라우팅, MCP streamable-http, OpenAI 역할별 모델)가 모두 실제 코드 위에서 동작함을 확인했고, 유일한 미충족 항목이던 라우팅 지연도 조치로 해소하여 현 아키텍처로 본 구현을 진행합니다.
- 판정 기준: 3개 항목 모두 성공 기준 충족 시 Go, 일부 실패 시 해당 요소 대안 결정 후 동일 절차로 재검증
- 본 구현 반영 사항:
  - 라우팅 지연 조치 완료: 라우터 모델 gpt-5-nano→gpt-5-mini 변경, reasoning_effort=minimal을 설정값(llm_router_reasoning_effort)으로 도입하여 지연을 목표 이내로 단축
  - finalizer 후보 gpt-5.1은 응답 지연·간결성 측면에서 양호하여 유력 후보로 확인, 최종 확정은 답변 품질 평가 후 결정
  - 실서버 tool은 DB 의존이므로 MCP 통합 검증은 DB 연동 완료 후 실데이터로 재수행
- 재검증 필요 항목: MCP 실서버 tool 왕복(DB 연동 후), 통합 환경에서 라우팅 지연 실측

## 11. 참고 자료

- docs/adr/0001-architecture.md: 아키텍처 결정 기록
- docs/agent/architecture.md: 에이전트 Supervisor 설계
- docs/mcp/realtime.md: mcp-realtime 설계
- src/agentory/common/events.py: SSE 이벤트 계약 단일 소스

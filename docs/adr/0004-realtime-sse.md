# ADR-0004: 실시간 스트리밍(SSE)·알림 동기화·트윈 상태 판정

- 상태: 승인 (2026-07-09)
- 결정자: 주희정
- 관련: [ADR-0001](0001-architecture.md), [docs/sse-events.md](../sse-events.md), feature/36-notification-sse(#37), feature/42-sse-cors-headers(#48), feature/60-realtime-status(#61), feature/76-notification-pagination(#77)

## 배경

디지털 트윈 대시보드는 챗 추론 스트림과 알림 벨 갱신 두 가지 실시간 채널이 필요합니다.
둘 다 서버에서 클라이언트로의 단방향 push만 요구하며, 별도 스케줄러·워커 인프라를 두기에는
4주 일정과 팀 규모가 빠듯합니다. 이 제약 아래 스트리밍 전송·알림 동기화·상태 판정 방식을
정했습니다.

## 결정

### 1. 전송은 SSE, 계약은 두 개로 분리

WebSocket 대신 SSE를 채택했습니다. 단방향 push만 필요하고, fetch·`EventSource` 표준과 자동
재연결·`Last-Event-ID` 이력 재개가 계약에 자연스럽게 들어맞기 때문입니다.

- 챗 스트림 `SSEEvent`: `thought → action → observation → (반복) → answer → done`, 오류 시
  `error → done`. `Field(discriminator="type")` 판별 유니온.
- 알림 스트림 `NotificationEvent`: 신규 알림 1건당 이벤트 1개. 챗과 별개 계약으로 분리해
  소비처(챗 창 vs 알림 벨)를 독립시킵니다.

계약의 단일 소스는 `src/agentory/common/events.py`이며, [docs/sse-events.md](../sse-events.md)와
`tests/contracts/`는 동기화 대상 사본입니다(AGENTS.md 규칙).

### 2. 알림 동기화는 sync-on-poll 커넥션 루프

별도 배치 워커 대신, 알림 스트림 커넥션 루프가 동기화 주기 잡을 겸합니다. 매 반복
(`STREAM_POLL_SECONDS`=3초)마다 변수별 알람 저널(EquipmentAlarm)을 notifications로 멱등 적재
(동일 설비+변수+알람은 30분 버킷당 1건으로 중복 제외)한 뒤 신규 알림을 push합니다. `after_id`로
마지막 수신 id 이후만 증분 방출해 재연결에 안전합니다.

### 3. 목록은 키셋(커서) 페이지네이션 (feature/76 #77)

알림은 실시간으로 상단에 삽입되므로 OFFSET은 중복·누락을 유발합니다. `(occurred_at, id)`를
base64로 인코딩한 불투명 커서로 발생 역순 키셋 페이지네이션을 적용합니다. 첫 페이지에서만
sync-on-read를 수행하고 더보기는 조회만 해 지연을 최소화합니다.

### 4. 트윈 상태 판정은 실시간 최신 tick 기준 (feature/60 #61)

상태는 설비별 최신 tick 한 건의 `alarm_code` 접두사로 판정합니다(없음=NORMAL, ERR*=CRITICAL,
그 외=WARNING). 초기에는 한 번 확정된 알람을 점검 전까지 유지하는 래치(sticky) 방식을
도입했으나(feature/54 #55), 3D 뷰(최신 tick)와 상세(래치값)의 **상태 소스 불일치**가
발생했습니다. 3D 뷰와 상세를 동일 소스로 일치시키는 단순성을 택해 판정을 최신 tick 기준으로
되돌렸습니다.

### 5. 스트림 오류는 계약 이벤트로 표면화 (feature/58 #59)

그래프 실행 예외가 빈 응답으로 새어 나가던 문제를 막기 위해, 예외를 커넥션 끊김이 아닌
`error → done` 계약 이벤트로 변환합니다(`GraphRecursionError`=`RECURSION_LIMIT`,
그 외=`AGENT_ERROR`). 스트리밍은 부분 답변이 없으면 assistant 메시지를 저장하지 않고,
비스트리밍(`run_query`)은 예외를 재발생시켜 대칭 처리합니다.

### 6. fetch 기반 SSE를 위한 CORS 확대 (feature/42 #48)

fetch로 SSE를 열면 `Cache-Control`·`Last-Event-ID` 등 비안전 헤더가 preflight로 전송되는데,
허용 목록이 `Authorization`·`Content-Type`뿐이라 CORS 차단이 발생했습니다. credentials 동반 시
Starlette가 요청 헤더를 echo하는 동작에 근거해 `allow_headers=["*"]`로 확대하고
`expose_headers=["Last-Event-ID"]`를 추가했습니다.

## 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| WebSocket | 양방향 불필요, 재연결·이력 재개 직접 구현 부담 |
| 별도 알림 배치 워커 | 스케줄러 인프라 추가, 커넥션 루프로 대체 가능 |
| OFFSET 페이지네이션 | 실시간 삽입으로 경계 밀림, 중복·누락 |
| 알람 래치 유지 | 3D 뷰와 상세 상태 소스 불일치, 단순성 우선해 폐기 |
| 예외를 커넥션 끊김으로 처리 | 사용자에게 빈 응답, 원인 코드 전달 불가 |

## 결과 / 미해결

- MCP realtime 서버는 REST와 동일한 `telemetry/repository.py`를 공유해 LLM 도구와 트윈 REST가
  같은 조회 로직을 씁니다(계약 중복 없음).
- 남은 리스크로 기록합니다.
  - 주기 실행 이상 감지 잡(`watcher/detector.py`)은 아직 `TODO(주희정)` 미구현이며, 현재
    이상 감지는 시뮬레이터 알람 코드에 의존합니다(실제 추세 감지 없음).
  - 래치 코드(`fetch_latched_alarm`·`clear-alarm` API·`alarm_cleared_at`)가 판정 경로에서
    빠졌으나 스캐폴딩으로 잔존합니다. `clear-alarm`은 실동작 API로 남아 혼란 소지가 있어
    폐기·보존 방침을 후속 정리 대상으로 둡니다.
  - 클라이언트마다 3초 폴링 + 매 반복 telemetry 전체 스캔이라 커넥션 증가 시 DB 부하가
    커집니다. 단일 동기화 소스(워처)로 이관 시 스트림은 순수 조회만 하도록 재설계할 여지가
    있습니다.
  - 상태를 최신 tick 기준으로 되돌리면서, 순간 정상 tick이 들어오면 경보가 사라질 수 있는
    래치의 안전성은 포기했습니다.
  - `allow_headers=["*"]` + credentials 조합의 안전성은 Starlette echo 동작에 의존하며,
    `test_cors_allows_sse_fetch_headers`가 회귀를 가드합니다.

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

### 3. 목록은 페이지 번호(OFFSET) 페이지네이션 (BE_NOTI01_SCOPE01)

당초에는 `(occurred_at, id)` 키셋 커서를 적용했습니다(feature/76 #77). 알림이 실시간으로 상단에
삽입되므로 OFFSET은 경계가 밀려 중복·누락을 유발한다는 판단이었습니다.

이후 화면 요구사항이 "더보기"에서 **페이지 번호 직접 이동**(1, 2, 3 ... 클릭)으로 바뀌면서 이
결정을 뒤집었습니다. 키셋 커서는 순차 이동만 가능해 임의 페이지로 점프할 수 없기 때문입니다.
발생 역순 정렬에 `offset`·`limit`을 적용하고, 화면이 페이지 번호를 렌더할 수 있도록 응답에
`total_items`·`total_pages`를 함께 반환합니다. 마지막 페이지를 넘는 요청은 빈 목록 대신 마지막
페이지로 보정합니다.

**포기한 것**: 조회 중 새 알림이 상단에 삽입되면 이후 페이지의 경계가 한 칸씩 밀립니다. 사용자가
2페이지를 보는 사이 신규 알림이 들어오면 1페이지 마지막 항목이 2페이지 첫 항목으로 다시 보일 수
있습니다. 알림은 30분 버킷당 1건으로 중복이 억제되어 삽입 빈도가 낮고, 이력 화면은 과거 조회가
주 용도라 실사용 영향이 작다고 판단해 페이지 번호 이동의 편의를 택했습니다. 경계 밀림이 문제가
되면 정렬 기준 시각을 조회 시점으로 고정(`occurred_at <= 최초 조회 시각`)하는 스냅샷 방식으로
보완할 수 있습니다.

### 3-1. 알림 조회는 담당 라인으로 서버 스코핑 (BE_NOTI01_SCOPE01)

목록·스트림 모두 로그인 사용자의 담당 라인(`user_lines`)으로 서버에서 걸러 반환합니다. 이전에는
서버가 전 라인을 방출하고 클라이언트가 메시지 본문과 설비 ID 접두사로 라인을 추정해 걸렀는데,
추정에 실패하면 통과시키는 구조라 담당 외 알림이 노출됐습니다.

라인 판별을 위해 알림 적재 시점에 설비 마스터의 `line_name`을 비정규화 보관합니다. 매 조회마다
`equipment_masters`·`lines`·`user_lines`를 3중 조인하는 대신, 3초 주기로 폴링하는 스트림의
조회 비용을 단일 인덱스 조건으로 낮추기 위함입니다. 담당 라인이 배정되지 않은 사용자는 알림을
받지 않으며(fail-close), `admin` 역할만 전 라인을 봅니다.

### 3-2. 읽음 상태는 사용자별로 분리 (BE_NOTI01_SCOPE01)

읽음 여부를 `notifications.is_read` 단일 플래그로 보관하던 구조에서는 한 사용자가 읽으면 모든
사용자에게 읽음으로 보였습니다. 읽음은 사용자마다 다른 상태이므로 `notification_reads`
(알림, 사용자) 연결 테이블로 분리하고, 행의 존재 여부로 판정합니다. 읽음 해제는 행 삭제입니다.

### 3-3. 알림 이력은 발생 시각 반열림 구간 필터, 경계는 프론트 산출 (BE_NOTI01_RANGE01)

알림 이력 화면의 캘린더에서 날짜(기간)를 선택하면 그 기간의 알림만 조회하는 요구가 생겼습니다.
`GET /notifications`에 선택값 `start`·`end`(ISO 8601, tz 포함)를 추가하고, 발생 시각을 반열림
구간 `[start, end)`로 필터합니다. `start`는 포함, `end`는 미포함이라 인접 기간을 이어 조회해도
경계에서 중복·누락이 생기지 않습니다. 두 값 모두 선택값이며, 미지정 시 기존처럼 전체를 조회해
캘린더를 쓰지 않는 화면과 하위호환을 유지합니다. 경계 역전(`start >= end`) 요청은 400으로
조기 차단합니다.

경계 시각은 서버가 아니라 프론트가 tz를 포함해 산출해 보냅니다. `occurred_at`은 `timestamptz`라
서버는 tz 가정 없이 값 비교만 수행하면 되고, 사용자 로컬 tz(KST)로 "하루"의 경계를 아는 주체가
화면이기 때문입니다. 예로 KST 8월 5일 하루는 프론트가
`start=2026-08-05T00:00:00+09:00`·`end=2026-08-06T00:00:00+09:00`으로 변환해 요청합니다. 이
방식은 이후 다른 tz 사용자가 생겨도 서버 조회 로직을 바꾸지 않습니다.

필터는 목록 조회와 총계 집계 양쪽에 동일 적용해 페이지네이션 총계가 정합을 유지하며, 정렬키
`occurred_at`이 기존 인덱스(`ix_notifications_occurred_at`·`ix_notifications_line_occurred_at`)를
그대로 타는 범위 스캔이라 신규 인덱스 없이 성립합니다.

**가용 날짜 조회**: 캘린더가 "알림이 있는 날짜만 선택 가능(나머지 회색)"으로 표시하려면 어느
날짜에 알림이 있는지 알아야 합니다. 이를 위해 `GET /notifications/available-dates`를 추가해 담당
라인 알림이 존재하는 KST 날짜 목록만 반환합니다. "하루"는 화면과 동일한 KST 기준으로 버킷팅하며
(`occurred_at`을 `Asia/Seoul`로 절단, DST 없어 고정 오프셋과 동일), 스캔 범위는 위 필터와 같은
`start`·`end` 선택값으로 가시 월에 한정합니다. 프론트가 가시 월 경계를 tz 포함해 보내면 그 구간
안 알림의 KST 날짜는 항상 해당 월이라 UTC 범위 비교와 KST 날짜 버킷이 어긋나지 않습니다.

**대체안**: 프론트가 목록 API를 전 페이지 순회해 알림 날짜를 클라이언트에서 추리던 종전 방식은
전체 이력을 over-fetch하는 순차 왕복이라 캘린더 표시가 느렸습니다. 서버 기간 필터와 가용 날짜
엔드포인트로 이 왕복을 각각 없앱니다. 가용 날짜 조회는 `occurred_at::date` 그룹화라 이론상 표현식
인덱스가 유리하나, 스캔을 가시 월로 한정하면 대상 행이 적어 기존 범위 인덱스로 충분하다고 보고
신규 인덱스는 두지 않습니다. 데이터 누적으로 월 조회가 느려지면 KST 날짜 표현식 인덱스를 후속
추가 여지로 남깁니다.

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
| 키셋 커서 페이지네이션 유지 | 페이지 번호 직접 이동 불가, 순차 더보기만 가능 (결정 3에서 OFFSET으로 전환) |
| 알림 라인 필터를 클라이언트에 유지 | 서버 payload에 라인 정보가 없어 문자열 추정에 의존, 판별 실패 시 통과라 누락 발생 |
| 조회마다 라인 3중 조인 | 스트림이 3초 주기로 폴링해 조인 비용이 지속 발생 |
| 알람 래치 유지 | 3D 뷰와 상세 상태 소스 불일치, 단순성 우선해 폐기 |
| 예외를 커넥션 끊김으로 처리 | 사용자에게 빈 응답, 원인 코드 전달 불가 |

## 결과 / 미해결

- MCP realtime 서버는 REST와 동일한 `telemetry/repository.py`를 공유해 LLM 도구와 트윈 REST가
  같은 조회 로직을 씁니다(계약 중복 없음).
- 남은 리스크로 기록합니다.
  - 주기 실행 이상 감지 잡(`watcher/detector.py`)은 본 ADR 작성 시점에 `TODO(주희정)` 미구현
    상태였고, 이상 감지가 시뮬레이터 알람 코드에만 의존해 실제 추세 감지가 없었습니다. 이후
    PCA MSPC 기반 스코어러를 도입해 해소했습니다(BE_ANOM01_SERVE01, `watcher/anomaly_worker.py`).
    임계 규칙을 대체하지 않고 레이어로 얹는 구조이며, 결정 근거와 실험 수치는
    [docs/experiments/decisions.md](../experiments/decisions.md)·[docs/anomaly-serving.md](../anomaly-serving.md)에
    있습니다. 기본값은 `anomaly_detection_enabled=false`·`anomaly_shadow_mode=true`라 관찰
    단계이며, 실발령(WRN-901) 전환은 섀도우 비교 후로 남아 있습니다.
  - 래치 코드(`fetch_latched_alarm`·`clear-alarm` API·`alarm_cleared_at`)가 판정 경로에서
    빠졌으나 스캐폴딩으로 잔존합니다. `clear-alarm`은 실동작 API로 남아 혼란 소지가 있어
    폐기·보존 방침을 후속 정리 대상으로 둡니다.
  - 당초에는 클라이언트마다 3초 폴링 + 매 반복 telemetry 전체 스캔이라 커넥션이 늘수록 DB
    부하가 커졌고, 단일 동기화 소스(워처)로의 이관을 재설계 여지로만 적어두었습니다. 실제로
    커넥션 풀 고갈이 발생해 이관을 실행했습니다(NEW_PROACT01_DETECT01).
    동기화는 `watcher/sync_worker.py`가 `notification_sync_interval_seconds`(기본 5초) 주기로
    단독 수행하고, 스트림·목록 조회는 순수 조회만 합니다. 조회 창은 최근 2시간으로 제한해
    풀스캔을 없앴으며(버킷 유니크로 멱등성 유지), 풀은 `pool_size=10`·`max_overflow=10`·
    `pool_timeout=10`·`pool_pre_ping`으로 고정했습니다.
  - 워처가 단일 동기화 소스가 되면서, 워처가 죽으면 알림 생성이 전면 중단됩니다. 개별 주기
    예외는 삼키고 다음 주기에 재시도하지만 루프 자체의 사망은 감시하지 않으며, 알림 지연은
    조회 창(2시간) 안에서만 자동 복구됩니다.
  - 상태를 최신 tick 기준으로 되돌리면서, 순간 정상 tick이 들어오면 경보가 사라질 수 있는
    래치의 안전성은 포기했습니다.
  - `allow_headers=["*"]` + credentials 조합의 안전성은 Starlette echo 동작에 의존하며,
    `test_cors_allows_sse_fetch_headers`가 회귀를 가드합니다.

# SSE 이벤트 계약 (BE_CHAT01_STREAM01)

백엔드 ↔ 프론트 스트리밍 계약. **단일 소스는 `src/agentory/common/events.py`** 이며
해당 문서는 프론트 참조용 사본이다. 변경 시 반드시 함께 갱신하고 `tests/contracts/test_sse_events.py` 를 통과시켜야 한다.

## 엔드포인트

```
POST /api/v1/chat/stream    (text/event-stream)
  body: { "message": "...", "session_id": "<uuid>" }
```

비스트리밍 응답은 `POST /api/v1/chat/query` (동일 body)로 전체 답변을 한 번에 반환한다.
SSE의 `event` 필드에 이벤트 타입, `data` 필드에 JSON 페이로드가 실린다.

## 이벤트 흐름

```
thought → action → observation → (반복) → answer(delta 스트림) → done
오류 시: error → done
```

## 이벤트 타입

| type | 페이로드 | 용도 |
| --- | --- | --- |
| `thought` | `{step, agent, content}` | 추론 단계 (FR-09, Thought 아코디언) |
| `action` | `{step, agent, tool, tool_input, reason?}` | 도구 호출. `reason`은 도구 선택 근거 (NEW_TRUST03) |
| `observation` | `{step, agent, tool, content}` | 도구 실행 결과 |
| `answer` | `{delta}` | 최종 답변 토큰 조각 (누적 렌더링) |
| `error` | `{code, message}` | 사용자 친화적 오류 |
| `done` | `{citations[], grounded?, suggested_questions[]}` | 종료. `citations`=출처 인용(NEW_TRUST01), `grounded`=자가 검증 결과(NEW_TRUST02), `suggested_questions`=후속 추천 질문(BE_CHAT02_SUGGEST01) |

- `agent` 값: `supervisor` \| `data_analysis` \| `knowledge` \| `maintenance`
- `citations[]` 항목: `{doc_id, snippet?, data_as_of?}`
- `suggested_questions[]` 항목: 후속 추천 질문 문자열, 프론트 퀵 리플라이 칩용 (없으면 빈 배열)

## 예시

```
event: action
data: {"type":"action","step":1,"agent":"data_analysis","tool":"get_sensor_logs","tool_input":{"line_name":"B라인"},"reason":"이상 설비 특정을 위해 최근 센서 로그 필요"}

event: done
data: {"type":"done","citations":[{"doc_id":"MAN-ETC-042","data_as_of":"2026-07-02T10:00:00+09:00"}],"grounded":true,"suggested_questions":["ERR-402 원인이 뭐야?","EQP-002 조치 방법 알려줘","유사 사례가 있었어?"]}
```

## 알림 실시간 스트림 (NEW_PROACT01_ALERT01)

챗 스트림과 별개 계약입니다. 상단 알림 벨·알림 이력의 실시간 갱신용이며, 신규 알림 1건당 이벤트 1개를 방출합니다.

```
GET /api/v1/notifications/stream?after_id=<마지막으로 받은 알림 id>    (text/event-stream)
```

`after_id` 이후 id의 알림만 오름차순으로 방출하며, 커넥션 유지 중 서버가 주기(약 3초)로 신규 알림을 감지해 밀어줍니다.

| type | 페이로드 | 용도 |
| --- | --- | --- |
| `notification` | `{id, occurred_at, equipment_id, metric, alarm_code, severity, message, is_read}` | 신규 알림 1건 (NEW_PROACT01_ALERT01) |

- `occurred_at`: 알림 발생 시각 (ISO 8601)
- `id`: 알림 식별자, 다음 재연결 시 `after_id`로 사용
- `metric`: 알람이 발생한 센서 변수 키(예: `temperature`), 변수 특정 불가 시 `null`
- `severity`: 알람 심각도 `주의`·`위험`, `alarm_code` 접두(`ERR`=위험·그 외=주의) 기준 서버 판정값입니다. 프론트는 코드로 재추론하지 말고 이 값을 그대로 사용합니다

```
event: notification
data: {"type":"notification","id":42,"occurred_at":"2026-07-06T10:02:00+09:00","equipment_id":"EQP-003","metric":"temperature","alarm_code":"ERR-402","severity":"위험","message":"EQP-003 냉각 이상 (온도 상승·압력 하강)","is_read":false}
```

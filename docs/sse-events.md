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
| `done` | `{citations[], grounded?}` | 종료. `citations`=출처 인용(NEW_TRUST01), `grounded`=자가 검증 결과(NEW_TRUST02) |

- `agent` 값: `supervisor` \| `data_analysis` \| `knowledge` \| `rediagnosis`
- `citations[]` 항목: `{doc_id, snippet?, data_as_of?}`

## 예시

```
event: action
data: {"type":"action","step":1,"agent":"data_analysis","tool":"get_sensor_logs","tool_input":{"line_name":"B-Line"},"reason":"이상 설비 특정을 위해 최근 센서 로그 필요"}

event: done
data: {"type":"done","citations":[{"doc_id":"MAN-ETC-042","data_as_of":"2026-07-02T10:00:00+09:00"}],"grounded":true}
```

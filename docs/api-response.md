# API 응답 규약 (ApiResponse)

모든 REST 엔드포인트는 성공·실패를 단일 래퍼 **ApiResponse**로 반환합니다. 프론트는 HTTP
상태코드로 성공 여부를 판정하고, 본문은 항상 아래 구조로 파싱합니다. 구현은
`src/agentory/common/response.py`(성공)와 전역 예외 핸들러 `src/agentory/main.py`(실패)입니다.

## 구조

| 필드 | 설명 |
| --- | --- |
| `success` | 성공 여부 (true/false) |
| `code` | 응답 코드, 성공은 `COMMON200`, 실패는 시맨틱 코드(`NOT_FOUND` 등) |
| `message` | 사용자 표시 메시지, 요청 로케일(Accept-Language)로 번역 |
| `result` | 성공 시 payload, 실패 시 null |

HTTP 상태코드는 실제값(200·201·400·404 등)을 그대로 유지합니다.

## 성공

```json
{ "success": true, "code": "COMMON200", "message": "성공입니다", "result": { } }
```

목록은 `result`가 배열, 생성(201)은 `result`에 생성 리소스, 본문 없는 처리(삭제·읽음)는
`result`가 null입니다. 기존 204 No-Content는 200 + `result: null`로 통일했습니다.

## 실패

```json
{ "success": false, "code": "NOT_FOUND", "message": "설비 없음: EQP-A01", "result": null }
```

`Accept-Language: en`이면 `message`가 영어(`Equipment not found: EQP-A01`)로 반환됩니다.
검증 오류(422)는 `code`가 `VALIDATION_ERROR`, `result`에 필드별 상세가 실립니다.

## 래핑 제외 대상

- SSE 스트림(`/chat/stream`, `/notifications/stream`): text/event-stream, 이벤트 스키마 고정
- 리다이렉트 응답(auth `/login/redirect`·`/callback` 등): 302 Location

## 남은 작업

auth 모듈의 성공 응답(로그인 URL·현재 사용자·로그아웃) 래핑은 담당자 협의 후 별도 반영 예정이며,
auth의 실패 응답은 전역 HTTPException 핸들러로 이미 ApiResponse 봉투로 통일되어 있습니다.

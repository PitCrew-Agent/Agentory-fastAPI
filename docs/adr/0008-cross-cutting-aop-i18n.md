# ADR-0008: 횡단 관심사 표준화 (예외·i18n·ApiResponse·액세스 로그)

- 상태: 승인 (2026-07-13), 감사 로그 격리 추가 (2026-07-24)
- 결정자: 주희정
- 관련: [ADR-0001](0001-architecture.md), [ADR-0004](0004-realtime-sse.md), [docs/api-response.md](../api-response.md), [docs/database.md](../database.md), feature/97(#98), feature/99(#100), feature/101(#102), feature/103(#104), feature/105(#106)

## 배경

라우터마다 `try/except`로 예외를 HTTP로 매핑(44곳)하고, 사용자 대면 문자열이 한국어 리터럴로
코드에 흩어져 있었으며, 성공 응답은 엔드포인트마다 리소스를 그대로 반환하고, 요청별 추적 수단이
없었습니다. 이 ADR은 이 횡단 관심사(cross-cutting concern)를 전역 인프라로 표준화한 일련의 결정을
한곳에 기록합니다. FastAPI에는 Spring식 AOP가 없으므로 전역 예외 핸들러·미들웨어·의존성과 얇은
공용 유틸로 같은 목적을 달성합니다.

## 결정

### 1. 예외 → HTTP 매핑 중앙화 (#98, #100)

도메인 코드는 리터럴 `HTTPException` 대신 도메인 예외를 raise하고, 전역 예외 핸들러가 code·
http_status·메시지로 직렬화합니다.

- 예외 계층 `common/exceptions.py`: `AppError` 기반 `NotFoundError(404)`·`PermissionDeniedError(403)`·
  `ValidationError(400)`·`ConflictError(409)`·`ExternalServiceError(502)`, 각 예외는 `message_code`(i18n 키)와
  `params`를 보유
- 라우터 44곳의 try/except 제거, 서비스는 도메인 예외 raise
- auth 모듈은 인증 프로토콜 오류·담당자 경계라 제외, 전역 `HTTPException` 핸들러로 응답만 통일

### 2. 메시지 국제화 (#98, #100, #106)

- 단문 에러·검증 메시지는 공용 카탈로그 `common/i18n.py`(메시지 코드 → ko/en), `translate(code, locale)`가
  요청 로케일로 번역
- 리스트형 도메인 텍스트(알람 문구·조치 체크리스트)는 각 도메인 모듈에 로케일별 딕셔너리로 co-locate
- 요청 로케일은 `Accept-Language` 헤더로 결정(`resolve_locale`), 지원 로케일은 ko·en, 미지원·미등록은 ko 폴백
- 로케일은 요청 컨텍스트 `contextvar`(`common/context.py`)로 전파해 서비스·핸들러가 인자 없이 참조
- 알림 메시지는 저장값 대신 읽기 시점에 코드+로케일로 재빌드(ko는 저장값과 동일, SSE 스키마 무변경)

### 3. 통일 응답 래퍼 ApiResponse (#102)

성공·실패를 단일 구조 `{success, code, message, result}`로 통일합니다(mediforme 컨벤션과 통일, 프론트
합의 완료). 상세 규약은 [docs/api-response.md](../api-response.md)입니다.

- 성공은 엔드포인트가 `ApiResponse.ok(result)` 반환 + `response_model=ApiResponse[T]`로 Swagger 정합
- 실패는 전역 핸들러가 `{success:false, ...}` ApiResponse 생성
- HTTP status는 실제값 유지, 204 No-Content는 200 + `result:null`로 전환
- SSE 스트림·리다이렉트는 래핑 제외

### 4. 요청 액세스 로그·추적 (#104)

- 순수 ASGI `ContextMiddleware`가 request_id·locale를 설정하고, 응답 완료 시 메서드·경로·상태·소요시간을
  액세스 로그로 남기며 `X-Request-ID` 응답 헤더를 부착
- 로그 포맷에 request_id 주입(`RequestIdFilter`)해 한 요청의 로그를 동일 id로 묶음
- 쿼리스트링은 토큰 노출 우려로 로그에서 제외

### 5. 감사 로그 적재 best-effort 격리 (2026-07-24 추가)

감사 로그 DB 적재 실패를 요청 처리와 분리합니다. `write_audit_event`의 `AuditLog` INSERT를
try/except로 감싸 실패 시 경고 로그와 `request.state.audit_db_error`만 남기고 요청은 계속
진행합니다. Redis 캐시 적재가 이미 같은 방식이었으므로 두 경로의 실패 처리를 맞춥니다.

계기는 2026-07-23 RDS 관리형 마스터 비밀번호 로테이션입니다. `DATABASE_URL`의 비밀번호가
옛 값으로 남아 DB 접속이 전부 실패했는데, 감사 로그 적재가 인증 미들웨어 경로에 있어
DB와 무관한 요청까지 함께 죽었습니다. 영향 범위 실측은 다음과 같습니다.

| 경로 | 사고 중 | 격리 후 기대값 | 비고 |
| --- | --- | --- | --- |
| `/api/v1/auth/login` | 500 | 200 | DB 불필요, IdP 인가 URL 발급만 수행 |
| `/api/v1/auth/login/redirect` | 500 | 307 | 위와 동일 |
| `/api/v1/auth/password-reset` | 500 | 200 | DB 불필요 |
| `/api/v1/auth/me` | 500 | 401 | 세션은 Redis 조회, 담당 라인만 DB |
| 존재하지 않는 `/api/v1/*` 경로 | 500 | 404 | 미들웨어가 라우팅 전에 실패 |
| `/health` | 200 | 200 | PUBLIC_PATHS라 감사 로그 미적재 |

DB 장애 지속 시간은 약 18시간(2026-07-23 16:10 로테이션 ~ 2026-07-24 11:53 복구)이었고,
그동안 dev API의 `/api/v1/*` 전 경로가 500이었습니다. 격리 후에는 DB가 필요한 엔드포인트만
실패하고 인증 흐름은 유지되므로, 같은 장애가 재발해도 로그인 자체는 가능합니다.

트레이드오프로 DB 장애 구간의 감사 로그는 유실됩니다. 감사 로그가 인증·인가 판단의 근거가
아니라 사후 추적용이고, Redis 캐시 경로가 남아 있어 단기 조회는 가능하므로 가용성을 우선했습니다.
유실 사실은 `audit log db write failed` 경고로 남겨 모니터링에서 포착할 수 있게 했습니다.

## 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| 라우터 try/except 유지 | 44곳 중복, 상태코드·i18n 산개 |
| 성공 응답 래핑 안 함(HTTP 네이티브) | 프론트 균일 파싱을 위해 팀 컨벤션으로 ApiResponse 채택 |
| 성공 응답을 body 재작성 미들웨어로 래핑 | SSE·스트림 body 파싱 충돌, OpenAPI 드리프트 |
| enum 라벨(상태·작업유형) 백엔드 번역 | 저장 canonical 유지가 옳음, 표시 변환은 프론트 담당 |
| i18n을 Babel gettext로 | 메시지 규모(~50)에 과함, 빌드 스텝 부담 |
| 감사 로그를 PUBLIC_PATHS에서만 끄기 | 사고 재현 시 인증 필요 경로는 그대로 전면 500, 근본 해결 아님 |
| 감사 로그 DB 적재를 백그라운드 태스크로 이관 | 실패 격리는 되나 유실이 조용해짐, 경고 로그 확보가 우선이라 보류 |
| 감사 로그 DB 적재 자체를 제거하고 Redis만 사용 | Redis TTL 만료 후 추적 불가, 감사 요건상 영속 저장 필요 |

## 결과 / 미해결

- 남은 작업으로 기록합니다.
  - auth 모듈 성공 응답 ApiResponse 래핑(리다이렉트·콜백 흐름 주의)은 담당자 협의 후 반영
  - 쓰기 작업 감사(audit) 데코레이터는 auth 소유 AuditLog 인프라와 조율 후 별도 진행
- enum 저장값이 한국어(대기/정기점검 등)라 프론트가 코드→라벨 변환을 담당합니다.

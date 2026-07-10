# ADR-0002: Azure AD SSO + opaque 세션 쿠키 인증

- 상태: 승인 (2026-07-09)
- 결정자: 주희정
- 관련: [ADR-0001](0001-architecture.md), feature/27-azure-sso-auth(#28), feature/auth-opaque-session(#43), fix/chat-session-owner-email(#49)

## 배경

사내 계정으로 로그인하는 Vue SPA와 FastAPI 백엔드 구조에서 인증 방식을 정해야 했습니다.
초기에는 프론트가 Azure AD에서 받은 id_token(JWT)을 Authorization 헤더로 실어 보내고
백엔드가 매 요청 JWKS로 검증하는 Bearer 방식으로 시작했으나, 구현을 진행하며 다음 약점이
드러났습니다.

- id_token이 브라우저의 JS 접근 가능 저장소에 노출되어 XSS·로그·Referer 유출 위험이 있습니다.
- 서버가 발급된 토큰을 즉시 무효화할 수단이 없어 만료 시각까지 유효합니다.
- 매 요청 JWKS 검증 비용이 발생합니다.

## 결정

인증 방식은 아래 순서로 세 번 전환하여 **opaque 세션 쿠키 + 서버측 세션(Redis)** 으로 정착했습니다.

| 단계 | 방식 | 브랜치 |
| --- | --- | --- |
| 1 | Bearer(id_token 직접 검증) | feature/27-azure-sso-auth(#28) |
| 2 | 콜백을 프론트로 리다이렉트, fragment로 토큰 전달해 노출 축소 | feature/27-auth-login-redirect(#40) |
| 3 | opaque 세션 쿠키 + Redis 서버측 세션 (최종) | feature/auth-opaque-session(#43) |

최종 방식의 요지는 다음과 같습니다.

1. 로그인 콜백에서 OIDC 토큰 교환을 수행한 뒤, 서버가 세션을 Redis에 생성하고 임의의 불투명
   세션 ID를 HttpOnly 쿠키로 발급합니다.
2. 클라이언트는 JWT를 보관하지 않으며, 이후 모든 요청은 세션 쿠키만 제시합니다.
3. 세션 값에는 필요한 클레임(email 등)만 저장하고 JWT 전체는 저장하지 않습니다.
4. OIDC 토큰 교환 실패 시 Azure 오류 응답을 로깅해 원인 추적을 가능하게 합니다.

## 세션 소유자 식별: sub → email

opaque 세션 값에 JWT 클레임 전체를 넣지 않기로 하면서 `sub` 키가 사라졌고, 이를 참조하던
chat·worklog 모듈이 세션 dict에 없는 키를 조회해 500을 유발했습니다. 소유자 식별자를
세션의 `email`로 통일했습니다(fix/chat-session-owner-email #49, feature/70-worklog-owner-sub-fix #71).
worklog의 컬럼·파라미터명은 `owner_sub`로 남아 있으나 실제 저장 값은 email이며, 이는 향후
실제 sub 도입 시 혼란을 줄 수 있는 명명 부채로 기록합니다.

## 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| Bearer(id_token) 유지 | 토큰 클라이언트 노출·즉시 무효화 불가·매 요청 JWKS 검증 |
| 자체 발급 JWT(stateless 세션) | 무효화 불가는 동일, 서명 키 관리 부담 추가 |
| Keycloak 등 별도 IdP 도입 | 사내 Azure AD 이미 존재, 4주 일정 대비 운영 인스턴스 과다 |
| 소유자 식별을 sub로 유지 | opaque 세션에 sub 미보관, email이 chat·worklog 공통 사용 가능 |

## 결과

- 세션 저장소는 Redis 단일 인스턴스이며, 로그아웃·강제 만료는 세션 삭제로 즉시 반영됩니다.
- 신규 사용자 자동 프로비저닝은 설정 플래그(`auth_auto_provision_enabled`)로 제어합니다.
- 운영 배포 전 되돌릴 로컬 기본값: `auth_cookie_secure`, `auth_cookie_samesite`,
  `auth_auto_provision_enabled`, `frontend_redirect_uri`·`cors_allow_origins`의 localhost 값.
- SSE(fetch 기반)와 세션 쿠키 병행을 위한 CORS 설정은 [ADR-0004](0004-realtime-sse.md) 참조.

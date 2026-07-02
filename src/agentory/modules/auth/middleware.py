"""OIDC 로그인·JWT 검증 미들웨어 (BE_AUTH01_OAUTH01)

Keycloak(IdP) 연동, 구현 후 main.py create_app()에서 미들웨어/의존성으로 등록
"""

from fastapi import Request


async def get_current_user(request: Request) -> dict | None:
    """JWT 검증 후 사용자 정보 반환, 미인가 시 401

    TODO(안민호): OIDC issuer JWKS 서명 검증, 만료/audience 체크 구현
    현재는 인증 미적용 플레이스홀더
    """
    return None

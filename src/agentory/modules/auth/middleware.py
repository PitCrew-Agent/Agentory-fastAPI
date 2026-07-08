"""OIDC 로그인·JWT 검증 미들웨어 (BE_AUTH01_OAUTH01)

Keycloak(IdP) 연동, 구현 후 main.py create_app()에서 미들웨어/의존성으로 등록
"""

from datetime import UTC, datetime
from time import monotonic
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError, PyJWK
from sqlalchemy import select
from starlette.responses import JSONResponse, Response

from agentory.core.config import get_settings
from agentory.core.db import SessionLocal
from agentory.modules.auth.models import AuditLog, SSOAccount, User
from agentory.modules.auth.redis_store import cache_audit_log, get_auth_session

PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
    "/api/v1/auth/login",
    "/api/v1/auth/login/redirect",
    "/api/v1/auth/signup",
    "/api/v1/auth/signup/redirect",
    "/api/v1/auth/callback",
    "/api/v1/auth/password-reset",
    "/api/v1/auth/password-reset/redirect",
    "/api/v1/auth/refresh",
}
JWKS_CACHE_TTL_SECONDS = 300
ALLOWED_ALGORITHMS = ["RS256"]

_oidc_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


def _auth_enabled() -> bool:
    settings = get_settings()
    return bool(settings.oidc_issuer_url and settings.oidc_client_id)


def _issuer_url() -> str:
    return get_settings().oidc_issuer_url.rstrip("/")


async def _get_oidc_metadata() -> dict[str, Any]:
    issuer_url = _issuer_url()
    cached = _oidc_metadata_cache.get(issuer_url)
    now = monotonic()
    if cached and now - cached[0] < JWKS_CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{issuer_url}/.well-known/openid-configuration")
        response.raise_for_status()
        metadata = response.json()

    _oidc_metadata_cache[issuer_url] = (now, metadata)
    return metadata


async def _get_jwks() -> dict[str, Any]:
    metadata = await _get_oidc_metadata()
    jwks_url = metadata.get("jwks_uri")
    if not jwks_url:
        raise AuthenticationError("missing jwks_uri")

    cached = _jwks_cache.get(jwks_url)
    now = monotonic()
    if cached and now - cached[0] < JWKS_CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(jwks_url)
        response.raise_for_status()
        jwks = response.json()

    _jwks_cache[jwks_url] = (now, jwks)
    return jwks


def _extract_session_id(request: Request) -> str:
    session_id = request.cookies.get(get_settings().auth_session_cookie_name)
    if not session_id:
        raise AuthenticationError("missing auth session")
    return session_id


def _session_user(session: dict[str, str]) -> dict[str, Any]:
    user = {
        "user_id": int(session["user_id"]),
        "email": session["email"],
        "name": session["name"],
        "role": session["role"],
        "status": session["status"],
    }
    if user["status"] != "active":
        raise AuthorizationError("user is not allowed")
    return user


def _select_jwk(jwks: dict[str, Any], kid: str | None) -> dict[str, Any]:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    raise AuthenticationError("unknown signing key")


async def verify_oidc_jwt(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:
        raise AuthenticationError("invalid token header") from exc

    jwks = await _get_jwks()
    jwk = _select_jwk(jwks, header.get("kid"))
    signing_key = PyJWK.from_dict(jwk).key

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=get_settings().oidc_client_id,
            issuer=_issuer_url(),
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except InvalidTokenError as exc:
        raise AuthenticationError("invalid token") from exc

    return claims


def _claim_email(claims: dict[str, Any]) -> str:
    provider_user_id = claims["sub"]
    return (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
        or f"{provider_user_id}@azure-ad.local"
    )


def _claim_name(claims: dict[str, Any], email: str) -> str:
    return claims.get("name") or email.split("@", 1)[0]


async def _load_local_user(claims: dict[str, Any]) -> User:
    provider_user_id = claims.get("sub")
    if not provider_user_id:
        raise AuthenticationError("missing subject")

    settings = get_settings()
    async with SessionLocal() as session:
        stmt = (
            select(User)
            .join(SSOAccount, SSOAccount.user_id == User.id)
            .where(
                SSOAccount.provider == settings.oidc_provider,
                SSOAccount.provider_user_id == provider_user_id,
            )
        )
        user = (await session.execute(stmt)).scalar_one_or_none()

        if user and user.status == "active":
            return user
        if user:
            raise AuthorizationError("user is not allowed")
        if not settings.auth_auto_provision_enabled:
            raise AuthorizationError("user is not allowed")

        email = _claim_email(claims)
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user:
            user = User(
                email=email,
                name=_claim_name(claims, email),
                role=settings.auth_default_role,
            )
            session.add(user)
            await session.flush()

        session.add(
            SSOAccount(
                user_id=user.id,
                provider=settings.oidc_provider,
                provider_user_id=provider_user_id,
                tenant_id=claims.get("tid"),
                provider_email=email,
            )
        )
        await session.commit()
        await session.refresh(user)

    if not user or user.status != "active":
        raise AuthorizationError("user is not allowed")
    return user


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


async def _write_audit_log(
    request: Request,
    *,
    status_code: int,
    success: bool,
    user_id: int | None = None,
    error_message: str | None = None,
) -> None:
    await write_audit_event(
        request,
        action="HTTP_REQUEST",
        status_code=status_code,
        success=success,
        user_id=user_id,
        error_message=error_message,
    )


async def write_audit_event(
    request: Request,
    *,
    action: str,
    status_code: int,
    success: bool,
    user_id: int | None = None,
    error_message: str | None = None,
) -> None:
    settings = get_settings()
    event: dict[str, Any] = {
        "user_id": user_id,
        "action": action,
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "ip_address": _client_ip(request),
        "user_agent": request.headers.get("user-agent"),
        "success": success,
        "error_message": error_message,
        "created_at": datetime.now(UTC).isoformat(),
    }

    if settings.audit_log_db_enabled:
        async with SessionLocal() as session:
            audit_log = AuditLog(
                user_id=event["user_id"],
                action=event["action"],
                method=event["method"],
                path=event["path"],
                status_code=event["status_code"],
                ip_address=event["ip_address"],
                user_agent=event["user_agent"],
                success=event["success"],
                error_message=event["error_message"],
            )
            session.add(audit_log)
            await session.flush()
            event["id"] = audit_log.id
            await session.commit()

    try:
        await cache_audit_log(event)
    except Exception as exc:
        request.state.audit_redis_error = str(exc)


async def oidc_auth_middleware(request: Request, call_next) -> Response:
    if request.url.path in PUBLIC_PATHS or not _auth_enabled():
        return await call_next(request)

    user_id: int | None = None
    try:
        session_id = _extract_session_id(request)
        session = await get_auth_session(session_id)
        if not session:
            raise AuthenticationError("invalid auth session")
        user = _session_user(session)
        user_id = user["user_id"]
        request.state.session_id = session_id
        request.state.auth_session = session
        request.state.user = user
    except (AuthenticationError, httpx.HTTPError):
        await _write_audit_log(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            success=False,
            error_message="Unauthorized",
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized"},
        )
    except AuthorizationError:
        await _write_audit_log(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            success=False,
            error_message="Forbidden",
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Forbidden"},
        )

    try:
        response = await call_next(request)
    except Exception:
        await _write_audit_log(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            success=False,
            user_id=user_id,
            error_message="Internal Server Error",
        )
        raise

    await _write_audit_log(
        request,
        status_code=response.status_code,
        success=response.status_code < 400,
        user_id=user_id,
    )
    return response


async def get_current_user(request: Request) -> dict[str, Any]:
    """미들웨어가 검증해 넣은 사용자 정보 반환, 미인가 시 401

    JWKS 서명·만료·audience 검증은 oidc_auth_middleware의 verify_oidc_jwt에서 수행
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    return user

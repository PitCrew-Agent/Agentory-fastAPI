import logging
from secrets import token_urlsafe
from time import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from agentory.core.config import get_settings
from agentory.modules.auth.middleware import _get_oidc_metadata, _load_local_user, verify_oidc_jwt
from agentory.modules.auth.models import User
from agentory.modules.auth.redis_store import (
    pop_auth_state,
    store_auth_state,
)
from agentory.modules.auth.schemas import AuthTokenResponse, AuthUrlResponse, AuthUserResponse

log = logging.getLogger(__name__)


def _client_secret() -> str:
    settings = get_settings()
    return settings.oidc_client_secret or settings.azure_ad_client_secret


def _user_response(user: User) -> AuthUserResponse:
    return AuthUserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
    )


async def build_authorization_url(flow: str) -> AuthUrlResponse:
    settings = get_settings()
    metadata = await _get_oidc_metadata()
    authorization_endpoint = metadata.get("authorization_endpoint")
    if not authorization_endpoint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC unavailable",
        )

    state = token_urlsafe(32)
    nonce = token_urlsafe(32)
    await store_auth_state(
        state=state,
        flow=flow,
        nonce=nonce,
        redirect_uri=settings.oidc_redirect_uri,
    )

    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "redirect_uri": settings.oidc_redirect_uri,
        "response_mode": "query",
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
    }
    if flow == "signup":
        params["prompt"] = "create"
    elif flow == "login":
        params["prompt"] = "select_account"

    return AuthUrlResponse(
        authorization_url=f"{authorization_endpoint}?{urlencode(params)}",
        state=state,
        flow=flow,
        provider=settings.oidc_provider,
    )


async def build_password_reset_url() -> AuthUrlResponse:
    settings = get_settings()
    password_reset_url = settings.oidc_password_reset_url
    if not password_reset_url:
        tenant_hint = settings.azure_ad_tenant_id or "organizations"
        password_reset_url = f"https://passwordreset.microsoftonline.com/?whr={tenant_hint}"

    return AuthUrlResponse(
        authorization_url=password_reset_url,
        flow="password_reset",
        provider=settings.oidc_provider,
    )


async def exchange_authorization_code(code: str, state: str) -> AuthTokenResponse:
    state_data = await pop_auth_state(state)
    if not state_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid auth state")

    metadata = await _get_oidc_metadata()
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC unavailable",
        )

    form = {
        "client_id": get_settings().oidc_client_id,
        "client_secret": _client_secret(),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_data["redirect_uri"],
        "scope": get_settings().oidc_scopes,
    }
    tokens = await _post_token_request(token_endpoint, form)
    claims = await _claims_from_tokens(tokens)

    nonce = state_data.get("nonce")
    if nonce and claims.get("nonce") != nonce:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid nonce")

    user = await _load_local_user(claims)
    return _token_response(tokens, user, claims)


async def refresh_tokens(
    *,
    refresh_token: str | None = None,
) -> AuthTokenResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing refresh token")

    metadata = await _get_oidc_metadata()
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC unavailable",
        )

    form = {
        "client_id": get_settings().oidc_client_id,
        "client_secret": _client_secret(),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": get_settings().oidc_scopes,
    }
    tokens = await _post_token_request(token_endpoint, form)
    claims = await _claims_from_tokens(tokens)
    user = await _load_local_user(claims)
    return _token_response(tokens, user, claims)


async def build_logout_url() -> str | None:
    metadata = await _get_oidc_metadata()
    endpoint = metadata.get("end_session_endpoint")
    if not endpoint:
        return None

    params = {"post_logout_redirect_uri": get_settings().oidc_post_logout_redirect_uri}
    return f"{endpoint}?{urlencode(params)}"


async def _post_token_request(token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            token_endpoint,
            data=form,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    if response.status_code >= 400:
        # Azure 실제 오류(AADSTS)는 서버 로그만 남기고 응답 본문엔 노출 금지
        log.warning(
            "OIDC token exchange failed status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC token exchange failed",
        )
    return response.json()


async def _claims_from_tokens(tokens: dict[str, Any]) -> dict[str, Any]:
    token = tokens.get("id_token") or tokens.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    try:
        return await verify_oidc_jwt(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc


def _token_response(
    tokens: dict[str, Any],
    user: User,
    claims: dict[str, Any],
) -> AuthTokenResponse:
    now = int(time())
    expires_in = tokens.get("expires_in")
    return AuthTokenResponse(
        token_type=tokens.get("token_type", "Bearer"),
        access_token=tokens["access_token"],
        expires_in=expires_in,
        id_token=tokens.get("id_token"),
        refresh_token=tokens.get("refresh_token"),
        access_token_expires_at=now + int(expires_in) if expires_in else None,
        id_token_expires_at=claims.get("exp"),
        refresh_token_cached=False,
        refresh_token_handle=None,
        user=_user_response(user),
    )


def build_session_payload(
    token_response: AuthTokenResponse,
    *,
    fallback_refresh_token: str | None = None,
) -> dict[str, Any]:
    refresh_token = token_response.refresh_token or fallback_refresh_token
    return {
        "user_id": token_response.user.id,
        "email": token_response.user.email,
        "name": token_response.user.name,
        "role": token_response.user.role,
        "status": token_response.user.status,
        "token_type": token_response.token_type,
        "id_token": token_response.id_token,
        "access_token": token_response.access_token,
        "refresh_token": refresh_token,
        "access_token_expires_at": token_response.access_token_expires_at,
        "id_token_expires_at": token_response.id_token_expires_at,
    }

"""Azure AD OAuth/OIDC client helpers."""

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from agentory.core.config import get_settings
from agentory.modules.auth.security import decode_unverified_jwt


def _authority() -> str:
    settings = get_settings()
    if settings.azure_authority:
        return settings.azure_authority.rstrip("/")
    if not settings.azure_tenant_id:
        raise HTTPException(status_code=500, detail="AZURE_TENANT_ID is not configured")
    return f"https://login.microsoftonline.com/{settings.azure_tenant_id}"


def build_authorize_url(state: str | None = None) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.azure_client_id,
        "response_type": "code",
        "redirect_uri": settings.azure_redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email User.Read",
    }
    if state:
        params["state"] = state
    return f"{_authority()}/oauth2/v2.0/authorize?{urlencode(params)}"


async def exchange_code_for_claims(code: str) -> dict[str, Any]:
    settings = get_settings()
    token_url = f"{_authority()}/oauth2/v2.0/token"
    data = {
        "client_id": settings.azure_client_id,
        "client_secret": settings.azure_client_secret,
        "code": code,
        "redirect_uri": settings.azure_redirect_uri,
        "grant_type": "authorization_code",
        "scope": "openid profile email User.Read",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(token_url, data=data)
    if response.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Azure token exchange failed")

    token_payload = response.json()
    id_token = token_payload.get("id_token")
    if not id_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Azure id token missing")

    claims = decode_unverified_jwt(id_token)
    _validate_claims(claims)
    return claims


def _validate_claims(claims: dict[str, Any]) -> None:
    settings = get_settings()
    aud = claims.get("aud")
    exp = claims.get("exp")
    tid = claims.get("tid")
    if aud != settings.azure_client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Azure audience")
    if settings.azure_tenant_id and tid != settings.azure_tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Azure tenant")
    if not isinstance(exp, int) or exp < int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Azure id token expired")

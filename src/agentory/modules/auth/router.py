from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from starlette.responses import RedirectResponse

from agentory.core.config import get_settings
from agentory.modules.auth import service
from agentory.modules.auth.middleware import get_current_user, write_audit_event
from agentory.modules.auth.redis_store import list_audit_logs
from agentory.modules.auth.schemas import (
    AuthTokenResponse,
    AuthUrlResponse,
    AuthUserResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshTokenRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


def _current_user_response(user: dict) -> AuthUserResponse:
    return AuthUserResponse(
        id=user["user_id"],
        email=user["email"],
        name=user["name"],
        role=user["role"],
        status="active",
    )


@router.get("/login", response_model=AuthUrlResponse)
async def login(request: Request) -> AuthUrlResponse:
    response = await service.build_authorization_url("login")
    await write_audit_event(
        request,
        action="AUTH_LOGIN_START",
        status_code=status.HTTP_200_OK,
        success=True,
    )
    return response


@router.get("/login/redirect")
async def login_redirect(request: Request) -> RedirectResponse:
    response = await login(request)
    return RedirectResponse(response.authorization_url)


@router.get("/signup", response_model=AuthUrlResponse)
async def signup(request: Request) -> AuthUrlResponse:
    response = await service.build_authorization_url("signup")
    await write_audit_event(
        request,
        action="AUTH_SIGNUP_START",
        status_code=status.HTTP_200_OK,
        success=True,
    )
    return response


@router.get("/signup/redirect")
async def signup_redirect(request: Request) -> RedirectResponse:
    response = await signup(request)
    return RedirectResponse(response.authorization_url)


@router.get("/password-reset", response_model=AuthUrlResponse)
async def password_reset(request: Request) -> AuthUrlResponse:
    response = await service.build_password_reset_url()
    await write_audit_event(
        request,
        action="AUTH_PASSWORD_RESET_START",
        status_code=status.HTTP_200_OK,
        success=True,
    )
    return response


@router.get("/password-reset/redirect")
async def password_reset_redirect(request: Request) -> RedirectResponse:
    response = await password_reset(request)
    return RedirectResponse(response.authorization_url)


def _frontend_redirect(fragment: str) -> RedirectResponse:
    # 브라우저가 콜백에 직접 도달하므로 프론트로 302 리다이렉트, 토큰은 fragment로만 전달
    base = get_settings().frontend_redirect_uri
    return RedirectResponse(f"{base}#{fragment}", status_code=status.HTTP_302_FOUND)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    # IdP 로그인 실패 시 code 대신 error 파라미터 전달, 프론트로 에러 전달
    if error or not code or not state:
        await write_audit_event(
            request,
            action="AUTH_CALLBACK_FAILURE",
            status_code=status.HTTP_400_BAD_REQUEST,
            success=False,
            error_message=error or "Missing authorization code",
        )
        return _frontend_redirect(f"error={quote(error or 'invalid_request')}")

    try:
        response = await service.exchange_authorization_code(code, state)
    except HTTPException as exc:
        await write_audit_event(
            request,
            action="AUTH_CALLBACK_FAILURE",
            status_code=exc.status_code,
            success=False,
            error_message="OIDC callback failed",
        )
        return _frontend_redirect("error=auth_failed")
    except Exception:
        await write_audit_event(
            request,
            action="AUTH_CALLBACK_FAILURE",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            success=False,
            error_message="OIDC callback failed",
        )
        return _frontend_redirect("error=server_error")

    await write_audit_event(
        request,
        action="AUTH_CALLBACK_SUCCESS",
        status_code=status.HTTP_200_OK,
        success=True,
        user_id=response.user.id,
    )
    # 프론트는 id_token을 Bearer로 사용, fragment로 전달해 로그·Referer 노출 방지
    # refresh_handle은 만료 후 POST /auth/refresh 재발급용, 사용자정보는 GET /auth/me
    fragment = f"id_token={quote(response.id_token or '')}"
    if response.refresh_token_handle:
        fragment += f"&refresh_handle={quote(response.refresh_token_handle)}"
    return _frontend_redirect(fragment)


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh(request: Request, req: RefreshTokenRequest) -> AuthTokenResponse:
    try:
        response = await service.refresh_tokens(
            refresh_token=req.refresh_token,
            refresh_token_handle=req.refresh_token_handle,
        )
    except HTTPException as exc:
        await write_audit_event(
            request,
            action="AUTH_REFRESH_FAILURE",
            status_code=exc.status_code,
            success=False,
            error_message="Refresh token exchange failed",
        )
        raise
    except Exception:
        await write_audit_event(
            request,
            action="AUTH_REFRESH_FAILURE",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            success=False,
            error_message="Refresh token exchange failed",
        )
        raise

    await write_audit_event(
        request,
        action="AUTH_REFRESH_SUCCESS",
        status_code=status.HTTP_200_OK,
        success=True,
        user_id=response.user.id,
    )
    return response


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    req: LogoutRequest,
    user: dict = Depends(get_current_user),
) -> LogoutResponse:
    logout_url, revoked = await service.build_logout_url(
        refresh_token=req.refresh_token,
        refresh_token_handle=req.refresh_token_handle,
    )
    await write_audit_event(
        request,
        action="AUTH_LOGOUT",
        status_code=status.HTTP_200_OK,
        success=True,
        user_id=user["user_id"],
    )
    return LogoutResponse(logout_url=logout_url, refresh_token_revoked=revoked)


@router.get("/me", response_model=AuthUserResponse)
async def me(user: dict = Depends(get_current_user)) -> AuthUserResponse:
    return _current_user_response(user)


@router.get("/audit-logs")
async def audit_logs(
    index: Literal["time", "user", "success", "status", "action"] = "time",
    value: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _: dict = Depends(require_admin),
) -> list[dict[str, str]]:
    return await list_audit_logs(index=index, value=value, limit=limit)

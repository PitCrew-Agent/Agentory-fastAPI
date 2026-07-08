from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from starlette.responses import RedirectResponse, Response

from agentory.core.config import get_settings
from agentory.modules.auth import service
from agentory.modules.auth.middleware import get_current_user, write_audit_event
from agentory.modules.auth.redis_store import (
    delete_auth_session,
    get_auth_session,
    list_audit_logs,
    store_auth_session,
    update_auth_session,
)
from agentory.modules.auth.schemas import (
    AuthUrlResponse,
    AuthUserResponse,
    LogoutResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_domain() -> str | None:
    domain = get_settings().auth_cookie_domain.strip()
    return domain or None


def _cookie_secure() -> bool:
    settings = get_settings()
    return settings.auth_cookie_secure or settings.app_env == "prod"


def _set_session_cookie(response: Response, session_id: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=session_id,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=_cookie_secure(),
        samesite=settings.auth_cookie_samesite,
        domain=_cookie_domain(),
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value="",
        max_age=0,
        httponly=True,
        secure=_cookie_secure(),
        samesite=settings.auth_cookie_samesite,
        domain=_cookie_domain(),
        path="/",
    )


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


def _frontend_redirect(error: str | None = None) -> RedirectResponse:
    # 브라우저가 콜백에 직접 도달하므로 프론트로 302 리다이렉트
    base = get_settings().frontend_redirect_uri
    if error:
        separator = "&" if "?" in base else "?"
        base = f"{base}{separator}auth_error={quote(error)}"
    return RedirectResponse(base, status_code=status.HTTP_302_FOUND)


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
        return _frontend_redirect(error or "invalid_request")

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
        return _frontend_redirect("auth_failed")
    except Exception:
        await write_audit_event(
            request,
            action="AUTH_CALLBACK_FAILURE",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            success=False,
            error_message="OIDC callback failed",
        )
        return _frontend_redirect("server_error")

    await write_audit_event(
        request,
        action="AUTH_CALLBACK_SUCCESS",
        status_code=status.HTTP_200_OK,
        success=True,
        user_id=response.user.id,
    )
    session_id = await store_auth_session(
        payload=service.build_session_payload(response),
        ttl_seconds=get_settings().auth_session_ttl_seconds,
    )
    redirect = _frontend_redirect()
    _set_session_cookie(redirect, session_id)
    return redirect


@router.post("/refresh", response_model=AuthUserResponse)
async def refresh(
    request: Request,
    response: Response,
) -> AuthUserResponse:
    session_id = request.cookies.get(get_settings().auth_session_cookie_name)
    session = await get_auth_session(session_id) if session_id else None
    if not session or not session.get("refresh_token"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid auth session",
        )
    try:
        token_response = await service.refresh_tokens(
            refresh_token=session["refresh_token"],
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
        user_id=token_response.user.id,
    )
    updated = await update_auth_session(
        session_id,
        payload=service.build_session_payload(
            token_response,
            fallback_refresh_token=session.get("refresh_token"),
        ),
        ttl_seconds=get_settings().auth_session_ttl_seconds,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid auth session",
        )
    _set_session_cookie(response, session_id)
    return token_response.user


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
) -> LogoutResponse:
    session_id = request.cookies.get(get_settings().auth_session_cookie_name)
    logout_url = await service.build_logout_url()
    deleted = await delete_auth_session(session_id) if session_id else False
    await write_audit_event(
        request,
        action="AUTH_LOGOUT",
        status_code=status.HTTP_200_OK,
        success=True,
        user_id=user["user_id"],
    )
    _clear_session_cookie(response)
    return LogoutResponse(logout_url=logout_url, refresh_token_revoked=deleted)


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

"""Authentication API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.config import get_settings
from agentory.core.db import get_session
from agentory.modules.auth.azure import build_authorize_url, exchange_code_for_claims
from agentory.modules.auth.dependencies import get_current_user
from agentory.modules.auth.schemas import (
    LogoutRequest,
    PasswordHelpRequest,
    PasswordHelpResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserResponse,
)
from agentory.modules.auth.service import (
    login_or_create_azure_user,
    refresh_access_token,
    revoke_refresh_token,
    write_audit_log,
)
from agentory.modules.auth.schemas import AuditLogCreate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/azure/login")
async def azure_login() -> RedirectResponse:
    return RedirectResponse(build_authorize_url())


@router.get("/azure/callback", response_model=TokenResponse)
async def azure_callback(
    code: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        claims = await exchange_code_for_claims(code)
        return await login_or_create_azure_user(session, claims)
    except Exception as exc:
        await write_audit_log(
            session,
            AuditLogCreate(action="LOGIN_FAILED", success=False, error_message=str(exc)),
        )
        raise


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    req: RefreshTokenRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        token = await refresh_access_token(session, req.refresh_token)
        await write_audit_log(session, AuditLogCreate(action="TOKEN_REFRESH", success=True))
        return token
    except ValueError as exc:
        await write_audit_log(
            session,
            AuditLogCreate(action="TOKEN_REFRESH", success=False, error_message=str(exc)),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    req: LogoutRequest,
    current_user: UserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await revoke_refresh_token(session, req.refresh_token)
    await write_audit_log(
        session,
        AuditLogCreate(user_id=current_user.id, action="LOGOUT", success=True),
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    return current_user


@router.post("/password/help", response_model=PasswordHelpResponse)
async def password_help(
    req: PasswordHelpRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PasswordHelpResponse:
    await write_audit_log(
        session,
        AuditLogCreate(
            action="PASSWORD_HELP_REQUEST",
            method=request.method,
            path=str(request.url.path),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=True,
        ),
    )
    return PasswordHelpResponse(
        message="Azure AD account passwords must be reset through the company account page.",
        reset_url=get_settings().password_reset_help_url,
    )

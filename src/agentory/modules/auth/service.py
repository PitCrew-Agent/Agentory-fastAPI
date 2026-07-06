"""Authentication service logic."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agentory.core.cache import RedisCache
from agentory.core.config import get_settings
from agentory.modules.auth.models import Admin, AuditLog, FieldEngineer, SsoAccount, User
from agentory.modules.auth.schemas import AuditLogCreate, TokenResponse, UserResponse
from agentory.modules.auth.security import create_access_token, create_refresh_token, hash_token


def _admin_emails() -> set[str]:
    return {
        email.strip().lower()
        for email in get_settings().admin_emails.split(",")
        if email.strip()
    }


async def write_audit_log(session: AsyncSession, data: AuditLogCreate) -> None:
    session.add(AuditLog(**data.model_dump()))
    await session.commit()


async def get_user_type(session: AsyncSession, user_id: int) -> str:
    admin = await session.get(Admin, user_id)
    if admin:
        return "ADMIN"
    field_engineer = await session.get(FieldEngineer, user_id)
    if field_engineer:
        return "FIELD_ENGINEER"
    return "UNASSIGNED"


async def issue_tokens(session: AsyncSession, user: User) -> TokenResponse:
    user_type = await get_user_type(session, user.id)
    access_token = create_access_token(user_id=user.id, email=user.email, user_type=user_type)
    refresh_token = create_refresh_token()
    await _store_refresh_token(refresh_token, user.id)
    await session.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=get_settings().access_token_expire_minutes * 60,
    )


async def login_or_create_azure_user(session: AsyncSession, claims: dict[str, Any]) -> TokenResponse:
    provider_user_id = claims.get("oid") or claims.get("sub")
    tenant_id = claims.get("tid")
    email = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
    name = claims.get("name") or email
    if not provider_user_id or not tenant_id or not email:
        raise ValueError("Required Azure claims are missing")

    normalized_email = str(email).lower()
    result = await session.execute(
        select(SsoAccount)
        .options(selectinload(SsoAccount.user))
        .where(
            SsoAccount.provider == "azure_ad",
            SsoAccount.provider_user_id == str(provider_user_id),
        )
    )
    sso_account = result.scalar_one_or_none()
    if sso_account:
        user = sso_account.user
    else:
        user = await _get_or_create_user(session, normalized_email, str(name))
        session.add(
            SsoAccount(
                user_id=user.id,
                provider="azure_ad",
                provider_user_id=str(provider_user_id),
                tenant_id=str(tenant_id),
                provider_email=normalized_email,
            )
        )

    await _ensure_user_profile(session, user, normalized_email)

    user.last_login_at = datetime.now(timezone.utc)
    await session.flush()
    await write_audit_log(
        session,
        AuditLogCreate(user_id=user.id, action="LOGIN_SUCCESS", success=True),
    )
    return await issue_tokens(session, user)


async def refresh_access_token(session: AsyncSession, refresh_token: str) -> TokenResponse:
    user_id = await _get_refresh_token_user_id(refresh_token)
    if not user_id:
        raise ValueError("Invalid refresh token")

    await _delete_refresh_token(refresh_token)
    user = await session.get(User, user_id)
    if not user:
        raise ValueError("Invalid refresh token")
    return await issue_tokens(session, user)


async def revoke_refresh_token(session: AsyncSession, refresh_token: str) -> None:
    await _delete_refresh_token(refresh_token)
    await session.commit()


async def to_user_response(session: AsyncSession, user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        status=user.status,
        user_type=await get_user_type(session, user.id),
    )


async def _get_or_create_user(session: AsyncSession, email: str, name: str) -> User:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        return user
    user = User(email=email, name=name, status="ACTIVE")
    session.add(user)
    await session.flush()
    return user


async def _ensure_user_profile(session: AsyncSession, user: User, email: str) -> None:
    admin = await session.get(Admin, user.id)
    field_engineer = await session.get(FieldEngineer, user.id)
    if email in _admin_emails():
        if field_engineer:
            await session.delete(field_engineer)
        if not admin:
            session.add(Admin(user_id=user.id, display_name=user.name))
    else:
        if admin:
            await session.delete(admin)
        if not field_engineer:
            session.add(FieldEngineer(user_id=user.id, display_name=user.name))
    await session.flush()


def _refresh_token_key(refresh_token: str) -> str:
    return f"auth:refresh:{hash_token(refresh_token)}"


async def _store_refresh_token(refresh_token: str, user_id: int) -> None:
    ttl_seconds = get_settings().refresh_token_expire_days * 24 * 60 * 60
    await RedisCache().setex(_refresh_token_key(refresh_token), ttl_seconds, str(user_id))


async def _get_refresh_token_user_id(refresh_token: str) -> int | None:
    value = await RedisCache().get(_refresh_token_key(refresh_token))
    return int(value) if value else None


async def _delete_refresh_token(refresh_token: str) -> None:
    await RedisCache().delete(_refresh_token_key(refresh_token))

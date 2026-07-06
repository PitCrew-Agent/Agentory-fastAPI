"""Authentication and authorization dependencies."""

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.db import get_session
from agentory.modules.auth.models import User
from agentory.modules.auth.schemas import UserResponse
from agentory.modules.auth.security import decode_jwt
from agentory.modules.auth.service import get_user_type, to_user_response


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = decode_jwt(authorization.split(" ", 1)[1])
    user_id = payload.get("sub")
    result = await session.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or user.status != "ACTIVE":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return await to_user_response(session, user)


def require_user_types(*allowed_user_types: str) -> Callable:
    async def dependency(
        current_user: UserResponse = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> UserResponse:
        user_type = await get_user_type(session, current_user.id)
        if user_type not in allowed_user_types:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return current_user

    return dependency


require_roles = require_user_types

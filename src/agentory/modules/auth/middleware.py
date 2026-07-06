"""Audit logging middleware for HTTP requests."""

from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import Request, Response
from starlette.responses import Response as StarletteResponse

from agentory.core.db import SessionLocal
from agentory.modules.auth.models import AuditLog
from agentory.modules.auth.security import decode_jwt


async def audit_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[StarletteResponse]],
) -> Response:
    started = perf_counter()
    response: StarletteResponse | None = None
    error_message: str | None = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        error_message = str(exc)
        raise
    finally:
        status_code = response.status_code if response else 500
        await _write_request_log(
            request=request,
            status_code=status_code,
            success=status_code < 400 and error_message is None,
            error_message=error_message,
            latency_ms=int((perf_counter() - started) * 1000),
        )


async def _write_request_log(
    *,
    request: Request,
    status_code: int,
    success: bool,
    error_message: str | None,
    latency_ms: int,
) -> None:
    user_id = _extract_user_id(request)
    path = request.url.path
    if path == "/health":
        return
    async with SessionLocal() as session:
        try:
            session.add(
                AuditLog(
                    user_id=user_id,
                    action=_action_for_status(status_code),
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    success=success,
                    error_message=error_message or f"latency_ms={latency_ms}",
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()


def _extract_user_id(request: Request) -> int | None:
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        payload = decode_jwt(authorization.split(" ", 1)[1])
        return int(payload["sub"])
    except Exception:
        return None


def _action_for_status(status_code: int) -> str:
    if status_code == 401:
        return "AUTH_REQUIRED"
    if status_code == 403:
        return "ACCESS_DENIED"
    return "API_REQUEST"

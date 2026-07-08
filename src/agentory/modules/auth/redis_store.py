from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from time import time
from typing import Any

from agentory.core.config import get_settings
from agentory.core.redis import get_redis_client


def _key(*parts: object) -> str:
    prefix = get_settings().redis_key_prefix.strip(":")
    return ":".join([prefix, *(str(part).strip(":") for part in parts)])


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _string_map(values: dict[str, Any]) -> dict[str, str]:
    return {key: "" if value is None else str(value) for key, value in values.items()}


async def store_refresh_token(
    *,
    user_id: int,
    refresh_token: str,
    provider: str,
    provider_user_id: str,
    ttl_seconds: int | None = None,
) -> str:
    settings = get_settings()
    redis = get_redis_client()
    ttl = ttl_seconds or settings.refresh_token_ttl_seconds
    now = int(time())
    handle = token_urlsafe(32)
    token_hash = _hash_token(refresh_token)
    token_key = _key("auth", "refresh", handle)
    user_index_key = _key("auth", "refresh", "user", user_id)

    await redis.hset(
        token_key,
        mapping=_string_map(
            {
                "handle": handle,
                "token_hash": token_hash,
                "refresh_token": refresh_token,
                "user_id": user_id,
                "provider": provider,
                "provider_user_id": provider_user_id,
                "created_at": now,
                "expires_at": now + ttl,
            }
        ),
    )
    await redis.expire(token_key, ttl)
    await redis.zadd(user_index_key, {handle: now + ttl})
    await redis.expire(user_index_key, ttl)
    return handle


async def get_refresh_token_session(refresh_token_handle: str) -> dict[str, str] | None:
    redis = get_redis_client()
    session = await redis.hgetall(_key("auth", "refresh", refresh_token_handle))
    return session or None


async def revoke_refresh_token(refresh_token_handle: str) -> bool:
    redis = get_redis_client()
    session = await redis.hgetall(_key("auth", "refresh", refresh_token_handle))
    if not session:
        return False

    await redis.delete(_key("auth", "refresh", refresh_token_handle))
    user_id = session.get("user_id")
    if user_id:
        await redis.zrem(_key("auth", "refresh", "user", user_id), refresh_token_handle)
    return True


async def store_auth_session(
    *,
    payload: dict[str, Any],
    ttl_seconds: int | None = None,
) -> str:
    settings = get_settings()
    redis = get_redis_client()
    ttl = ttl_seconds or settings.auth_session_ttl_seconds
    now = int(time())
    session_id = token_urlsafe(32)
    session_key = _key("auth", "session", session_id)
    session_payload = {
        **payload,
        "session_id": session_id,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + ttl,
    }

    await redis.hset(session_key, mapping=_string_map(session_payload))
    await redis.expire(session_key, ttl)
    return session_id


async def get_auth_session(session_id: str) -> dict[str, str] | None:
    redis = get_redis_client()
    session_key = _key("auth", "session", session_id)
    session = await redis.hgetall(session_key)
    if not session:
        return None
    expires_at = int(session.get("expires_at") or 0)
    if expires_at <= int(time()):
        await redis.delete(session_key)
        return None
    return session


async def update_auth_session(
    session_id: str,
    *,
    payload: dict[str, Any],
    ttl_seconds: int | None = None,
) -> bool:
    existing = await get_auth_session(session_id)
    if not existing:
        return False

    settings = get_settings()
    redis = get_redis_client()
    ttl = ttl_seconds or settings.auth_session_ttl_seconds
    now = int(time())
    session_key = _key("auth", "session", session_id)
    session_payload = {
        **existing,
        **payload,
        "session_id": session_id,
        "updated_at": now,
        "expires_at": now + ttl,
    }

    await redis.hset(session_key, mapping=_string_map(session_payload))
    await redis.expire(session_key, ttl)
    return True


async def delete_auth_session(session_id: str) -> bool:
    redis = get_redis_client()
    return bool(await redis.delete(_key("auth", "session", session_id)))


async def store_auth_state(*, state: str, flow: str, nonce: str, redirect_uri: str) -> None:
    settings = get_settings()
    redis = get_redis_client()
    state_key = _key("auth", "state", state)
    await redis.hset(
        state_key,
        mapping=_string_map(
            {
                "state": state,
                "flow": flow,
                "nonce": nonce,
                "redirect_uri": redirect_uri,
                "created_at": int(time()),
            }
        ),
    )
    await redis.expire(state_key, settings.auth_state_ttl_seconds)


async def pop_auth_state(state: str) -> dict[str, str] | None:
    redis = get_redis_client()
    state_key = _key("auth", "state", state)
    data = await redis.hgetall(state_key)
    if data:
        await redis.delete(state_key)
    return data or None


async def cache_audit_log(event: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.audit_log_redis_enabled:
        return

    redis = get_redis_client()
    ttl = settings.audit_log_redis_ttl_seconds
    now_ms = int(time() * 1000)
    cutoff_ms = now_ms - (ttl * 1000)
    entry_id = event.get("id") or await redis.incr(_key("audit", "seq"))
    log_key = _key("audit", "log", entry_id)
    user_id = event.get("user_id") or "anonymous"
    success = "1" if event.get("success") else "0"
    status_code = event.get("status_code") or "unknown"
    action = event.get("action") or "unknown"

    payload = {
        **event,
        "id": entry_id,
        "created_at": event.get("created_at") or datetime.now(UTC).isoformat(),
    }

    await redis.hset(log_key, mapping=_string_map(payload))
    await redis.expire(log_key, ttl)
    await redis.xadd(
        _key("audit", "stream"),
        _string_map(payload),
        maxlen=settings.audit_log_redis_max_stream_length,
        approximate=True,
    )

    index_keys = [
        _key("audit", "index", "time"),
        _key("audit", "index", "user", user_id),
        _key("audit", "index", "success", success),
        _key("audit", "index", "status", status_code),
        _key("audit", "index", "action", action),
    ]
    for index_key in index_keys:
        await redis.zadd(index_key, {entry_id: now_ms})
        await redis.zremrangebyscore(index_key, 0, cutoff_ms)
        await redis.expire(index_key, ttl)


async def list_audit_logs(
    *,
    index: str = "time",
    value: str | None = None,
    limit: int = 50,
) -> list[dict[str, str]]:
    redis = get_redis_client()
    if index == "time":
        index_key = _key("audit", "index", "time")
    elif index in {"user", "success", "status", "action"} and value:
        index_key = _key("audit", "index", index, value)
    else:
        return []

    entry_ids = await redis.zrevrange(index_key, 0, limit - 1)
    logs: list[dict[str, str]] = []
    for entry_id in entry_ids:
        log = await redis.hgetall(_key("audit", "log", entry_id))
        if log:
            logs.append(log)
        else:
            await redis.zrem(index_key, entry_id)
    return logs

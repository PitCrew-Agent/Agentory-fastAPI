"""Minimal async Redis client for cache-style data with TTL."""

import asyncio
from urllib.parse import urlparse

from agentory.core.config import get_settings


class RedisCache:
    def __init__(self, url: str | None = None):
        parsed = urlparse(url or get_settings().redis_url)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 6379
        self.db = int((parsed.path or "/0").lstrip("/") or "0")

    async def get(self, key: str) -> str | None:
        response = await self._execute("GET", key)
        return response.decode("utf-8") if isinstance(response, bytes) else None

    async def setex(self, key: str, seconds: int, value: str) -> None:
        await self._execute("SETEX", key, str(seconds), value)

    async def delete(self, key: str) -> None:
        await self._execute("DEL", key)

    async def _execute(self, *parts: str) -> bytes | int | None:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            if self.db:
                writer.write(_encode_command("SELECT", str(self.db)))
                await writer.drain()
                await _read_response(reader)
            writer.write(_encode_command(*parts))
            await writer.drain()
            return await _read_response(reader)
        finally:
            writer.close()
            await writer.wait_closed()


def _encode_command(*parts: str) -> bytes:
    payload = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        encoded = part.encode("utf-8")
        payload.append(f"${len(encoded)}\r\n".encode("ascii"))
        payload.append(encoded + b"\r\n")
    return b"".join(payload)


async def _read_response(reader: asyncio.StreamReader) -> bytes | int | None:
    prefix = await reader.readexactly(1)
    if prefix == b"+":
        await reader.readline()
        return None
    if prefix == b":":
        return int((await reader.readline()).decode("ascii").strip())
    if prefix == b"$":
        length = int((await reader.readline()).decode("ascii").strip())
        if length == -1:
            return None
        data = await reader.readexactly(length)
        await reader.readexactly(2)
        return data
    if prefix == b"-":
        message = (await reader.readline()).decode("utf-8").strip()
        raise RuntimeError(f"Redis error: {message}")
    raise RuntimeError("Unsupported Redis response")

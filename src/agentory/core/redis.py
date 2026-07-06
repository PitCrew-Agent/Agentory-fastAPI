from functools import lru_cache

from redis.asyncio import Redis

from agentory.core.config import get_settings


@lru_cache
def get_redis_client() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


async def ping_redis() -> bool:
    return bool(await get_redis_client().ping())

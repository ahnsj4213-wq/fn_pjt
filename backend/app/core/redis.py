"""Shared Redis client (ADR-0013). Redis only carries hand-offs and notifications; every fact it carries is also in
PostgreSQL, so losing Redis data delays work by seconds and never loses it."""
import redis.asyncio as aioredis

from app.core.config import get_settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis | None:
    """None when `redis_url` is empty (Redis switched off)."""
    global _client
    url = get_settings().redis_url
    if not url:
        return None
    if _client is None:
        _client = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=2, health_check_interval=30)
    return _client


def set_redis(client: aioredis.Redis | None) -> None:
    """Tests swap the client (or reset it with None)."""
    global _client
    _client = client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

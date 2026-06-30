from __future__ import annotations

import redis.asyncio as redis

from core.config import settings

# Один клиент на приложение. redis.asyncio держит пул соединений и потокобезопасен
# в рамках одного event loop. decode_responses=True → строки вместо bytes (удобно с JSON).
redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> redis.Redis:
    """FastAPI-зависимость: общий Redis-клиент."""
    return redis_client
from __future__ import annotations

from redis.asyncio import Redis


async def check_rate_limit(redis: Redis, key: str, limit: int, window: int) -> bool:
    """Скользящий счётчик в Redis: разрешено ли действие.

    Паттерн INCR+EXPIRE: на первом обращении ставим TTL окна; счётчик
    автоматически обнуляется по истечении окна. Возвращает True, если
    в пределах лимита, иначе False.

    Атомарность: INCR атомарен; EXPIRE ставим только при count==1.
    (Гонка на грани окна возможна, но для лимитов запросов несущественна —
    «по-взрослому» делается одним Lua-скриптом.)
    """
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    return count <= limit
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from redis.asyncio import Redis

from core.contracts import Infra
from core.ratelimit import check_rate_limit


class InfraMiddleware(BaseMiddleware):
    """Кладёт Infra в data хендлеров (Dependency Injection на каждый апдейт).

    Хендлер, объявивший параметр `infra: Infra`, получит инфраструктуру
    автоматически — не нужно импортировать зависимости глобально.
    """

    def __init__(self, infra: Infra) -> None:
        self.infra = infra

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["infra"] = self.infra
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Ограничение частоты сообщений на пользователя (Redis INCR+EXPIRE).

    Защищает бота от спама и дорогих команд (/ask, /match далее). При
    превышении лимита хендлер не вызывается — отвечаем предупреждением.
    """

    def __init__(self, redis: Redis, limit: int, window: int) -> None:
        self.redis = redis
        self.limit = limit
        self.window = window

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            key = f"ratelimit:{user.id}"
            allowed = await check_rate_limit(self.redis, key, self.limit, self.window)
            if not allowed:
                if isinstance(event, Message):
                    await event.answer("Слишком часто. Подожди немного и повтори.")
                return None
        return await handler(event, data)
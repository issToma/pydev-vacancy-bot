from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.contracts import Infra


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
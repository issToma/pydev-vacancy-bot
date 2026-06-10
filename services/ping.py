from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.contracts import Infra


class PingService:
    """Демо-сервис: реализует контракт ServiceModule.

    Нужен на шаге 1, чтобы проверить сеам (реестр → /help → middleware).
    На шаге 2 заменим первым настоящим сервисом (Vacancy Radar).
    """

    name = "ping"
    commands = ["/ping"]
    help_text = "/ping — проверка живости бота"

    def register(self, router: Router, infra: Infra) -> None:
        @router.message(Command("ping"))
        async def ping(message: Message) -> None:
            await message.answer("pong")

    def api_router(self) -> None:
        return None
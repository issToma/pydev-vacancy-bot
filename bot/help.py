from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.contracts import ServiceModule


def build_help_router(services: list[ServiceModule]) -> Router:
    """Строит роутер с /help, собирая help_text всех сервисов из реестра.

    /help автоматически синхронизирован с набором сервисов — добавил сервис,
    его описание появилось в /help без правок здесь.
    """
    router = Router(name="help")
    text = "Доступные команды:\n\n" + "\n".join(s.help_text for s in services)

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        await message.answer(text)

    return router
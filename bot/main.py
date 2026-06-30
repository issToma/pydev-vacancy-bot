import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.help import build_help_router
from bot.middlewares import InfraMiddleware
from bot.registry import SERVICES, validate_registry
from core.config import settings
from core.infra import build_infra
from services.vacancy.notifications import run_notifier

logging.basicConfig(level=logging.INFO)


def build_core_router() -> Router:
    """Базовые команды бота, не привязанные к сервисам."""
    router = Router(name="core")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Привет! Я vacancy-bot. Список команд — /help"
        )

    return router


def build_dispatcher() -> Dispatcher:
    validate_registry(SERVICES)          # инвариант: уникальные name/команды
    infra = build_infra()

    dp = Dispatcher()
    # DI: infra доступна во всех хендлерах через data["infra"]
    dp.message.middleware(InfraMiddleware(infra))

    dp.include_router(build_core_router())

    # Роутеры сервисов — итерацией по реестру (ядро не знает конкретных сервисов)
    for service in SERVICES:
        router = Router(name=service.name)
        service.register(router, infra)
        dp.include_router(router)

    dp.include_router(build_help_router(SERVICES))   # авто-/help из реестра
    return dp


async def main() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    dp = build_dispatcher()

    # Прямой RabbitMQ-consumer уведомлений — фоновой задачей внутри бота.
    # Доставка идёт через bot.send_message; ошибки → DLQ (см. notifications.py).
    async def send(user_id: int, text: str) -> None:
        await bot.send_message(user_id, text, disable_web_page_preview=True)

    notifier_task = asyncio.create_task(run_notifier(send))

    logging.info("Бот запускается (long polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        notifier_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BufferedInputFile, Message

from core import s3

from bot.help import build_help_router
from bot.middlewares import FlowStateGuardMiddleware, InfraMiddleware, RateLimitMiddleware
from bot.registry import SERVICES, validate_registry
from core.config import settings
from core.infra import build_infra
from core.tg_html import HTML_SOURCE_LIMIT, TELEGRAM_TEXT_LIMIT, to_telegram_html
from services.dewarp.service import build_result_kb
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

    # FSM-состояние диалогов — в Redis (переживает перезапуск бота)
    dp = Dispatcher(storage=RedisStorage.from_url(infra.settings.redis_url))
    # rate-limit раньше всего (отсекаем спам до полезной работы), затем DI infra
    dp.message.middleware(
        RateLimitMiddleware(
            infra.redis,
            infra.settings.rate_limit_messages,
            infra.settings.rate_limit_window,
        )
    )
    # DI: infra доступна во всех хендлерах через data["infra"]
    dp.message.middleware(InfraMiddleware(infra))
    # смена намерения: другая команда снимает ожидание ввода (/dewarp, /match)
    dp.message.middleware(FlowStateGuardMiddleware())

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

    # Меню команд Telegram («Menu» у поля ввода) — собираем из реестра сервисов.
    menu = [cmd for service in SERVICES for cmd in service.commands]
    await bot.set_my_commands(menu)

    # Прямой RabbitMQ-consumer уведомлений — фоновой задачей внутри бота.
    # deliver решает, как доставить payload: текст (подписки) или фото из S3 (dewarp).
    # Ошибки доставки → DLQ (см. notifications.py).
    async def deliver(payload: dict) -> None:
        user_id = payload["user_id"]
        if payload.get("photo_key"):
            data = await s3.get_object(payload["photo_bucket"], payload["photo_key"])
            # по умолчанию — фото (превью в чате) + кнопка получить полное качество файлом
            await bot.send_photo(
                user_id,
                BufferedInputFile(data, filename="dewarped.jpg"),
                caption=payload.get("caption", ""),
                reply_markup=build_result_kb(payload["photo_key"]),
            )
        else:
            text = payload["text"]
            # рендерим Markdown→Telegram-HTML (для /match и т.п.); при сбое парсинга — plain
            try:
                await bot.send_message(
                    user_id, to_telegram_html(text[:HTML_SOURCE_LIMIT]),
                    parse_mode="HTML", disable_web_page_preview=True,
                )
            except TelegramBadRequest:
                await bot.send_message(
                    user_id, text[:TELEGRAM_TEXT_LIMIT], disable_web_page_preview=True
                )

    notifier_task = asyncio.create_task(run_notifier(deliver))

    logging.info("Бот запускается (long polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        notifier_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
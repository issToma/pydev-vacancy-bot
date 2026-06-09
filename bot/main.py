import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

from core.config import settings

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Привет! Я vacancy-bot. Пока умею только здороваться — "
        "скоро научусь искать вакансии, выпрямлять документы и отвечать на вопросы."
    )


async def main() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    logging.info("Бот запускается (long polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
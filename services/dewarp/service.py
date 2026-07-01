from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.config import settings
from core.contracts import Infra
from core.models import Job


class DewarpFlow(StatesGroup):
    waiting_image = State()      # после /dewarp бот ждёт изображение


class DewarpFileCB(CallbackData, prefix="dwf"):
    """Кнопка «получить результат файлом»: key — S3-ключ результата (в output-бакете)."""
    key: str


def build_result_kb(result_key: str) -> InlineKeyboardMarkup:
    """Клавиатура под фото-результатом: кнопка отдать полное качество файлом."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📄 Получить файлом",
            callback_data=DewarpFileCB(key=result_key).pack(),
        )
    ]])


class DewarpService:
    """Dewarp: по команде /dewarp пользователь присылает документ → выпрямленное изображение."""

    name = "dewarp"
    commands = [BotCommand(command="dewarp", description="выпрямить документ (фото или файл)")]
    help_text = (
        "/dewarp — затем пришли документ фото или файлом, верну выпрямленную версию.\n"
        "  Для лучшего качества входа отправляй как файл (без сжатия Telegram)."
    )

    def register(self, router: Router, infra: Infra) -> None:
        async def _enqueue(message: Message, file_id: str) -> None:
            buf = await message.bot.download(file_id)
            data = buf.read()

            job_id = uuid.uuid4()
            input_key = f"{job_id}.jpg"
            await infra.s3.put_object(settings.s3_bucket_input, input_key, data, "image/jpeg")

            async with infra.session_factory() as session:
                session.add(Job(
                    id=job_id, user_id=message.from_user.id,
                    service="dewarp", kind="dewarp", status="queued",
                    params={"input_key": input_key},
                ))
                await session.commit()

            # ставим задачу в Celery (по имени — без импорта worker-кода в бот)
            infra.celery.send_task(
                "services.dewarp.tasks.dewarp_task",
                args=[str(job_id), message.from_user.id, input_key],
            )
            await message.answer("Документ получил, обрабатываю — пришлю результат.")

        @router.message(Command("dewarp"))
        async def start_dewarp(message: Message, state: FSMContext) -> None:
            await state.set_state(DewarpFlow.waiting_image)
            await message.answer(
                "Пришли документ фото или файлом — верну выпрямленную версию.\n"
                "Лучше как файл (📎 → File), без сжатия. /cancel — отмена."
            )

        @router.message(DewarpFlow.waiting_image, Command("cancel"))
        async def cancel_dewarp(message: Message, state: FSMContext) -> None:
            await state.clear()
            await message.answer("Отменил.")

        # изображения обрабатываем ТОЛЬКО в состоянии ожидания (после /dewarp)
        @router.message(DewarpFlow.waiting_image, F.photo)
        async def on_photo(message: Message, state: FSMContext) -> None:
            await state.clear()
            await _enqueue(message, message.photo[-1].file_id)

        @router.message(DewarpFlow.waiting_image, F.document)
        async def on_document(message: Message, state: FSMContext) -> None:
            if not (message.document.mime_type or "").startswith("image/"):
                await message.answer("Это не изображение. Пришли фото/файл-картинку или /cancel.")
                return
            await state.clear()
            await _enqueue(message, message.document.file_id)

        # кнопка «получить файлом» под фото-результатом
        @router.callback_query(DewarpFileCB.filter())
        async def send_as_file(callback: CallbackQuery, callback_data: DewarpFileCB) -> None:
            data = await infra.s3.get_object(settings.s3_bucket_output, callback_data.key)
            await callback.message.answer_document(
                BufferedInputFile(data, filename="dewarped.jpg"),
                caption="Полное качество",
            )
            await callback.answer()

    def api_router(self) -> None:
        return None
from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from core.contracts import Infra
from core.models import Job
from services.download.formats import QUALITIES

# Статус задачи → значок для списка загрузок.
STATUS_ICONS = {
    "queued": "⏳", "running": "⬇️", "done": "✅", "failed": "❌",
    "skipped": "⏭",          # ту же ссылку в том же качестве уже качает другая задача
}

HISTORY_LIMIT = 10


class DownloadFlow(StatesGroup):
    waiting_link = State()       # раздел открыт — ждём ссылку


class DownloadCB(CallbackData, prefix="dl"):
    """Выбор качества. job — уже созданная запись Job, из неё воркер возьмёт ссылку.

    Ссылку в callback_data класть нельзя: лимит Telegram — 64 байта, а URL легко
    длиннее. UUID задачи укладывается с запасом.
    """
    job: str
    quality: str


def build_quality_kb(job_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Клавиатура выбора качества под сообщением со ссылкой (по 2 кнопки в ряд)."""
    buttons = [
        InlineKeyboardButton(
            text=label, callback_data=DownloadCB(job=str(job_id), quality=key).pack()
        )
        for key, label in QUALITIES.items()
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


class DownloadService:
    """Download: /dl <ссылка> — скачать видео или аудио на диск машины.

    Файл НЕ отправляется в Telegram (лимит бота — 50 МБ): он остаётся в папке
    DOWNLOAD_DIR, а в чат приходит только ход выполнения и название готового файла.
    """

    name = "download"
    BTN_LIST = "📂 Мои загрузки"
    menu_buttons = [BTN_LIST]
    commands = [
        BotCommand(command="dl", description="скачать видео по ссылке"),
        BotCommand(command="downloads", description="последние загрузки"),
    ]
    help_text = (
        "Пришли ссылку на видео — скачаю его на диск машины и сообщу, когда будет готово.\n"
        "Файл останется в папке загрузок, в чат он не отправляется."
    )

    async def on_enter(self, message: Message, state: FSMContext) -> None:
        """Хук навигации: при входе в раздел сразу ждём ссылку (без лишней кнопки)."""
        await state.set_state(DownloadFlow.waiting_link)

    def register(self, router: Router, infra: Infra) -> None:
        async def _offer_qualities(message: Message, url: str) -> None:
            """Создать запись задачи и предложить выбрать качество."""
            job_id = uuid.uuid4()
            async with infra.session_factory() as session:
                session.add(Job(
                    id=job_id, user_id=message.from_user.id,
                    service="download", kind="download", status="new",
                    params={"url": url},
                ))
                await session.commit()
            await message.answer(
                f"Что скачиваем?\n{url}", reply_markup=build_quality_kb(job_id),
                disable_web_page_preview=True,
            )

        async def _show_history(message: Message) -> None:
            async with infra.session_factory() as session:
                rows = (await session.execute(
                    select(Job)
                    .where(
                        Job.service == "download",
                        Job.user_id == message.from_user.id,
                        Job.status != "new",          # выбор качества так и не сделан
                    )
                    .order_by(Job.created_at.desc())
                    .limit(HISTORY_LIMIT)
                )).scalars().all()

            if not rows:
                await message.answer("Загрузок пока не было.")
                return

            lines = ["📂 Последние загрузки:"]
            for job in rows:
                params = job.params or {}
                icon = STATUS_ICONS.get(job.status, "•")
                title = params.get("title") or params.get("url", "")
                quality = QUALITIES.get(params.get("quality", ""), "")
                detail = ""
                if job.status == "done" and job.result_ref:
                    # jobs — история задач: она не знает, что стало с файлом потом.
                    # Без проверки диска удалённое видео висело бы в списке с галочкой.
                    # Верно, пока бот и воркер на одной машине (сейчас так и есть).
                    # Само имя файла не показываем: название уже в строке выше, а в
                    # имени к нему приклеен id ролика — он для файловой системы.
                    if not Path(job.result_ref).exists():
                        icon, detail = "🗑", "файла нет на диске"
                elif job.status == "failed" and params.get("error"):
                    detail = params["error"][:120]

                lines.append(f"{icon} {title[:60]} · {quality}")
                if detail:
                    lines.append(f"   {detail}")
            await message.answer("\n".join(lines), disable_web_page_preview=True)

        # ── команды ──
        @router.message(Command("dl"))
        async def cmd_dl(message: Message, command: CommandObject, state: FSMContext) -> None:
            url = (command.args or "").strip()
            if not url:
                await state.set_state(DownloadFlow.waiting_link)
                await message.answer("Пришли ссылку на видео")
                return
            if not url.startswith(("http://", "https://")):
                await message.answer("Это не похоже на ссылку — нужен адрес, начинающийся с http")
                return
            await _offer_qualities(message, url)

        @router.message(Command("downloads"))
        async def cmd_downloads(message: Message) -> None:
            await _show_history(message)

        # ── кнопка раздела (до обработчика свободного текста) ──
        @router.message(F.text == self.BTN_LIST)
        async def btn_list(message: Message) -> None:
            await _show_history(message)

        # ── ссылка свободным текстом в режиме ожидания ──
        # состояние НЕ сбрасываем: можно кидать ссылки подряд, выход — «☰ Меню»
        @router.message(DownloadFlow.waiting_link)
        async def on_link(message: Message) -> None:
            url = (message.text or "").strip()
            if not url.startswith(("http://", "https://")):
                await message.answer("Нужна ссылка, начинающаяся с http")
                return
            await _offer_qualities(message, url)

        # ── выбор качества → постановка задачи в очередь ──
        @router.callback_query(DownloadCB.filter())
        async def on_quality(callback: CallbackQuery, callback_data: DownloadCB) -> None:
            job_id = uuid.UUID(callback_data.job)
            async with infra.session_factory() as session:
                job = await session.get(Job, job_id)
                # job.user_id — не формальность: callback_data приходит от клиента,
                # и чужой id задачи сюда подставить можно. Чужую не трогаем.
                if job is None or job.user_id != callback.from_user.id:
                    await callback.answer("Задача не найдена", show_alert=True)
                    return
                if job.status != "new":
                    await callback.answer("Эта задача уже запущена", show_alert=True)
                    return
                url = (job.params or {}).get("url", "")
                job.status = "queued"
                job.params = {**(job.params or {}), "quality": callback_data.quality}
                await session.commit()

            # сообщение с кнопками превращаем в строку статуса: её же воркер будет
            # править по ходу загрузки (message_id уходит в задачу).
            # Правка может не пройти (сообщение старше 48 ч, удалено) — это не повод
            # отменять загрузку, просто дальше прогресс будет некуда писать.
            #
            # У кнопки старше 48 ч Telegram присылает callback.message как
            # InaccessibleMessage (это НЕ Message, метода edit_text у него нет) либо
            # вовсе ничего. Без проверки типа был бы AttributeError мимо
            # except TelegramBadRequest: задача не ставилась, в чат не приходило ничего.
            message = callback.message
            if isinstance(message, Message):
                try:
                    await message.edit_text(
                        f"⏳ В очереди · {QUALITIES.get(callback_data.quality, '')}\n{url}",
                        disable_web_page_preview=True,
                    )
                except TelegramBadRequest:
                    pass
            # 0 = «править нечего»: воркер тогда шлёт статусы новыми сообщениями
            progress_id = message.message_id if message is not None else 0
            # задача ставится по имени — воркерный код в бот не импортируется
            infra.celery.send_task(
                "services.download.tasks.download_task",
                args=[
                    str(job_id), callback.from_user.id, progress_id,
                    url, callback_data.quality,
                ],
            )
            await callback.answer()

    def api_router(self) -> None:
        # скачивание управляется только из бота; REST-эндпоинтов у сервиса нет
        return None

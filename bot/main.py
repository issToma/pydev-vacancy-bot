import asyncio
import contextlib
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import FSInputFile


from bot.help import build_help_router
from bot.middlewares import (
    AllowlistMiddleware,
    FlowStateGuardMiddleware,
    InfraMiddleware,
    RateLimitMiddleware,
)
from bot.navigation import build_fallback_router, build_navigation_router
from bot.registry import SERVICES, validate_registry
from core.config import settings
from core.infra import build_infra
from core.tg_html import HTML_SOURCE_LIMIT, TELEGRAM_TEXT_LIMIT, to_telegram_html
from services.vacancy.notifications import run_notifier_supervised

logging.basicConfig(level=logging.INFO)


async def _startup_call(
    coro_factory, *, what: str, errors: tuple[type[Exception], ...],
    attempts: int = 5, base_delay: float = 2.0,
):
    """Стартовый сетевой вызов с ретраями и экспоненциальным бэкоффом.

    До start_polling любой внешний запрос (Telegram set_my_commands) при
    кратковременном сетевом блипе роняет процесс целиком. Long
    polling обрывы после старта переживает сам — а вот эти до-polling вызовы нет,
    поэтому оборачиваем их. Ретраим только сетевые ошибки из `errors` (не логические:
    напр. S3 ClientError — это валидный ответ, его пробрасываем сразу).
    coro_factory — фабрика (lambda), создающая свежую корутину на каждую попытку."""
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except errors as exc:
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            logging.warning(
                "Сеть недоступна (%s), попытка %d/%d — повтор через %.0fс: %s",
                what, attempt, attempts, delay, exc,
            )
            await asyncio.sleep(delay)


# Telegram показывает в плеере только MP4. mkv/webm — а их отдаёт yt-dlp, когда
# склеивает VP9/AV1, — sendVideo не принимает, и файл терялся в DLQ.
PLAYABLE_VIDEO_SUFFIXES = {".mp4", ".mov"}
MEDIA_SEND_ATTEMPTS = 3
# Потолок у файла в чат — 48 МБ, на медленном аплинке дефолтных 60с не хватает.
MEDIA_SEND_TIMEOUT = 300


async def _report_send_failure(bot: Bot, user_id: int, path: Path, exc: Exception) -> None:
    """Сообщить, что файл скачан, но в чат не уехал.

    Молчать нельзя: «✅ Готово» пользователь уже получил и решит, что всё хорошо.
    """
    logging.error("Не удалось отправить %s в чат: %s", path.name, exc)
    await bot.send_message(
        user_id,
        f"⚠️ Файл скачан, но отправить его в чат не получилось\n"
        f"{str(exc)[:200]}\n"
        f"Он лежит в папке загрузок: {path.name}",
        disable_web_page_preview=True,
    )


async def _send_media(bot: Bot, payload: dict) -> None:
    """Отправить готовый файл: аудио, видео или документ — с запасными вариантами.

    Три уровня отказа. Сетевой блип — повтор с бэкоффом: файл уже на диске, терять
    его из-за разрыва незачем. Telegram не принял как видео — досылаем документом
    (без плеера, зато доходит). Не вышло ни так ни так — пишем об этом текстом.
    """
    user_id = payload["user_id"]
    path = Path(payload["media_path"])
    caption = payload.get("caption", "")[:1024]      # лимит подписи Telegram
    thumb = payload.get("thumb_path")
    if thumb and not Path(thumb).exists():
        # повторная доставка (например, вручную из DLQ): временный кадр уже убран
        # первой попыткой, и FSInputFile упал бы на открытии несуществующего файла
        thumb = None
    is_audio = bool(payload.get("is_audio"))
    as_document = not is_audio and path.suffix.lower() not in PLAYABLE_VIDEO_SUFFIXES

    async def send() -> None:
        # FSInputFile читает с диска потоком — файл целиком в память не грузим.
        if is_audio:
            await bot.send_audio(
                user_id, FSInputFile(path), caption=caption,
                title=payload.get("caption"), request_timeout=MEDIA_SEND_TIMEOUT,
            )
        elif as_document:
            await bot.send_document(
                user_id, FSInputFile(path), caption=caption,
                thumbnail=FSInputFile(thumb) if thumb else None,
                request_timeout=MEDIA_SEND_TIMEOUT,
            )
        else:
            await bot.send_video(
                user_id, FSInputFile(path), caption=caption,
                supports_streaming=True,              # плеер вместо «документа»
                width=payload.get("width"), height=payload.get("height"),
                duration=payload.get("duration"),
                thumbnail=FSInputFile(thumb) if thumb else None,
                request_timeout=MEDIA_SEND_TIMEOUT,
            )

    try:
        for attempt in range(1, MEDIA_SEND_ATTEMPTS + 1):
            try:
                await send()
                return
            except TelegramNetworkError as exc:
                if attempt == MEDIA_SEND_ATTEMPTS:
                    await _report_send_failure(bot, user_id, path, exc)
                    return
                delay = 2.0 * 2 ** (attempt - 1)
                logging.warning(
                    "Отправка %s не удалась (попытка %d/%d), повтор через %.0fс: %s",
                    path.name, attempt, MEDIA_SEND_ATTEMPTS, delay, exc,
                )
                await asyncio.sleep(delay)
            except TelegramBadRequest as exc:
                if as_document:          # документом уже пробовали — дальше некуда
                    await _report_send_failure(bot, user_id, path, exc)
                    return
                logging.warning(
                    "Telegram не принял %s как видео (%s) — отправляю документом",
                    path.name, exc,
                )
                as_document = True
    finally:
        if thumb:
            # служебный кадр из /tmp больше не нужен; само видео не трогаем
            with contextlib.suppress(OSError):
                Path(thumb).unlink()


def build_dispatcher() -> Dispatcher:
    validate_registry(SERVICES)          # инвариант: уникальные name/команды
    infra = build_infra()

    # FSM-состояние диалогов — в Redis (переживает перезапуск бота)
    dp = Dispatcher(storage=RedisStorage.from_url(infra.settings.redis_url))
    # allowlist — самым первым: посторонний не должен доходить даже до rate-limit
    dp.message.middleware(AllowlistMiddleware(infra.settings.allowed_ids))
    dp.callback_query.middleware(AllowlistMiddleware(infra.settings.allowed_ids))
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
    # смена намерения: другая команда снимает ожидание ввода (/match, ссылка на видео)
    dp.message.middleware(FlowStateGuardMiddleware())

    # навигация (главное меню + разделы) — включаем первой: ловит /start, /menu,
    # кнопки разделов раньше сервисных хендлеров
    dp.include_router(build_navigation_router(SERVICES))

    # Роутеры сервисов — итерацией по реестру (ядро не знает конкретных сервисов)
    for service in SERVICES:
        router = Router(name=service.name)
        service.register(router, infra)
        dp.include_router(router)

    dp.include_router(build_help_router(SERVICES))   # авто-/help из реестра
    dp.include_router(build_fallback_router())        # ПОСЛЕДНИМ: нераспознанный ввод
    return dp


async def main() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    dp = build_dispatcher()

    # Глобальное /-меню по умолчанию — пустое: на главном уровне навигация идёт
    # reply-клавиатурой, а команды раздела навигация выставляет per-chat при входе.
    await _startup_call(
        lambda: bot.set_my_commands([]), what="set_my_commands",
        errors=(TelegramNetworkError,),
    )

    # Прямой RabbitMQ-consumer уведомлений — фоновой задачей внутри бота.
    # deliver решает, как доставить payload: текст (подписки) или готовый файл (скачивание).
    # Ошибки доставки → DLQ (см. notifications.py).
    async def deliver(payload: dict) -> None:
        user_id = payload["user_id"]
        if payload.get("edit_message_id"):
            # ход выполнения (скачивание видео): правим одно сообщение, а не плодим
            # новые. «Текст не изменился» или удалённое сообщение — не повод
            # отклонять доставку в DLQ: прогресс не критичен.
            try:
                await bot.edit_message_text(
                    payload["text"][:TELEGRAM_TEXT_LIMIT], chat_id=user_id,
                    message_id=payload["edit_message_id"],
                    disable_web_page_preview=True,
                )
            except TelegramBadRequest:
                pass
        elif payload.get("media_path"):
            # готовая загрузка, влезающая в лимит Bot API (48 МБ): отдаём файлом,
            # чтобы смотреть/слушать можно было из любой сети, а не только дома
            await _send_media(bot, payload)
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

    notifier_task = asyncio.create_task(run_notifier_supervised(deliver))

    logging.info("Бот запускается (long polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        notifier_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
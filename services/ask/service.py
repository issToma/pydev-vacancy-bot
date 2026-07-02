from __future__ import annotations

import json
import logging
import time

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, Message

from core.contracts import Infra
from core.models import LlmUsage
from core.ratelimit import check_rate_limit
from core.tg_html import HTML_SOURCE_LIMIT, TELEGRAM_TEXT_LIMIT, to_telegram_html
from core.usage import over_budget, usage_summary

logger = logging.getLogger(__name__)

EDIT_INTERVAL = 1.0            # не чаще ~1 правки/сек (иначе rate-limit Telegram)


def _dialog_key(user_id: int) -> str:
    return f"dialog:{user_id}"


async def _load_history(redis, user_id: int) -> list[dict]:
    """Память диалога из Redis: список сообщений [{role, content}, ...]."""
    raw = await redis.get(_dialog_key(user_id))
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


async def _save_history(redis, user_id: int, history: list[dict], max_turns: int, ttl: int) -> None:
    """Сохранить последние max_turns реплик; TTL обновляется на каждый вызов."""
    trimmed = history[-max_turns:]
    await redis.set(_dialog_key(user_id), json.dumps(trimmed, ensure_ascii=False), ex=ttl)


async def _safe_edit(message: Message, text: str) -> None:
    """Отредактировать сообщение (plain), гася 'message is not modified' и лимит длины."""
    text = text[:TELEGRAM_TEXT_LIMIT]
    if not text:
        return
    try:
        await message.edit_text(text)
    except TelegramBadRequest:
        # идентичный текст (нечего менять) или прочий безобидный отказ редактирования
        pass


async def _finalize(message: Message, text: str) -> None:
    """Финальный ответ: рендерим Markdown→Telegram-HTML; при сбое парсинга — plain."""
    if not text:
        return
    html_text = to_telegram_html(text[:HTML_SOURCE_LIMIT])
    try:
        await message.edit_text(html_text, parse_mode="HTML")
    except TelegramBadRequest:
        await _safe_edit(message, text)   # откат на чистый текст


async def _record_usage(session_factory, user_id: int, model: str, usage) -> None:
    """Записать расход токенов в llm_usage (cost-control). Ошибку учёта не роняем на юзера."""
    if usage is None:
        return
    try:
        async with session_factory() as session:
            session.add(LlmUsage(
                user_id=user_id, service="ask", model=model,
                tokens_in=usage.prompt_tokens or 0,
                tokens_out=usage.completion_tokens or 0,
            ))
            await session.commit()
    except Exception:
        logger.exception("Не удалось записать llm_usage")


class AskService:
    """Ask: /ask <вопрос> — Q&A по разработке через LLM со streaming-ответом.

    Прямой async-вызов (НЕ Celery): ответ редактируется в чате по мере генерации.
    Память диалога — в Redis (последние N реплик), чтобы работали уточнения.
    """

    name = "ask"
    commands = [
        BotCommand(command="ask", description="вопрос по программированию (LLM)"),
        BotCommand(command="usage", description="мой расход токенов LLM за сутки"),
    ]
    help_text = (
        "/ask <вопрос> — точечный вопрос по программированию (в стиле StackOverflow).\n"
        "  Помнит контекст последних реплик — можно уточнять. Напр.: /ask чем RabbitMQ отличается от Kafka\n"
        "/usage — сколько токенов LLM ты потратил за последние сутки"
    )

    def register(self, router: Router, infra: Infra) -> None:
        settings = infra.settings

        @router.message(Command("ask"))
        async def ask(message: Message, command: CommandObject) -> None:
            question = (command.args or "").strip()
            if not question:
                await message.answer(
                    "Использование: /ask <вопрос>\n"
                    "Напр.: /ask как в SQLAlchemy сделать ORM-запрос к PostgreSQL"
                )
                return
            if not settings.llm_api_key:
                await message.answer("LLM не настроен: добавь LLM_API_KEY в .env")
                return

            user_id = message.from_user.id
            # отдельный, более строгий лимит для дорогой /ask (сверх общего rate-limit)
            allowed = await check_rate_limit(
                infra.redis, f"ratelimit:{user_id}:ask",
                settings.ask_rate_limit, settings.ask_rate_limit_window,
            )
            if not allowed:
                await message.answer(
                    f"Лимит /ask исчерпан ({settings.ask_rate_limit} за "
                    f"{settings.ask_rate_limit_window // 60} мин). Попробуй позже."
                )
                return
            # спенд-лимит: суточный бюджет токенов (контроль стоимости)
            async with infra.session_factory() as session:
                if await over_budget(session, user_id, settings.llm_daily_token_budget):
                    await message.answer(
                        "Дневной лимит токенов LLM исчерпан. Загляни в /usage; попробуй завтра."
                    )
                    return

            history = await _load_history(infra.redis, user_id)
            messages = [
                {"role": "system", "content": settings.ask_system_prompt},
                *history,
                {"role": "user", "content": question},
            ]

            placeholder = await message.answer("⌛ думаю…")

            kwargs: dict = {
                "model": settings.llm_model,
                "max_tokens": settings.ask_max_tokens,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},   # токены приходят в финальном чанке
            }
            # у «думающих» моделей отключаем рассуждения, чтобы не резать ответ
            if settings.ask_reasoning_effort:
                kwargs["reasoning_effort"] = settings.ask_reasoning_effort

            acc = ""
            usage = None
            last_edit = 0.0
            try:
                stream = await infra.llm.chat.completions.create(**kwargs)
                async for chunk in stream:
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage                    # чанк с usage обычно без choices
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content or ""
                    if not delta:
                        continue
                    acc += delta
                    now = time.monotonic()
                    if now - last_edit >= EDIT_INTERVAL:
                        last_edit = now
                        await _safe_edit(placeholder, acc)     # промежуточно — plain (безопасно)
                await _finalize(placeholder, acc or "(пустой ответ)")   # финал — HTML
            except Exception as e:
                logger.exception("Ошибка LLM в /ask")
                await _safe_edit(placeholder, f"⚠️ Ошибка LLM: {e}")
                return

            # сохраняем диалог только при успешном ответе
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": acc})
            await _save_history(
                infra.redis, user_id, history,
                settings.ask_dialog_max_turns, settings.ask_dialog_ttl,
            )
            # учёт расхода токенов (cost-control)
            await _record_usage(infra.session_factory, user_id, settings.llm_model, usage)

        @router.message(Command("usage"))
        async def usage_cmd(message: Message) -> None:
            async with infra.session_factory() as session:
                rows = await usage_summary(session, message.from_user.id)
            if not rows:
                await message.answer("За последние сутки запросов к LLM не было.")
                return
            lines = ["📊 Твой расход LLM за сутки:"]
            total_in = total_out = total_n = 0
            for service, n, t_in, t_out in rows:
                lines.append(f"• {service}: {n} запр., токены {t_in}+{t_out}")
                total_in += t_in
                total_out += t_out
                total_n += n
            budget = settings.llm_daily_token_budget
            tail = f" из {budget} лимита" if budget else ""
            lines.append(f"Итого: {total_n} запр., {total_in + total_out} токенов{tail}")
            await message.answer("\n".join(lines))

    def api_router(self) -> None:
        # /ask — только через бота (интерактивный streaming), REST-эндпоинта нет
        return None
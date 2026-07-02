from __future__ import annotations

import json

from aiogram import Router
from aiogram.filters import Command
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
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.contracts import Infra
from core.db import get_session
from core.ratelimit import check_rate_limit
from core.redis import get_redis
from core.usage import over_budget
from services.vacancy.repo import (
    VacancyFilters,
    VacancyRow,
    search_vacancies,
    top_technologies,
)
from services.vacancy.filters_ui import register_filter_dialog
from services.vacancy.subs import (
    create_subscription,
    deactivate_subscription,
    list_user_subscriptions,
    upsert_user,
)

STACK_CACHE_KEY = "cache:stack"


class MatchFlow(StatesGroup):
    waiting_resume = State()      # после /match бот ждёт текст резюме


class SubDelCB(CallbackData, prefix="subdel"):
    """Кнопка удаления подписки: id — идентификатор подписки."""
    id: int


def _subs_keyboard(subs) -> InlineKeyboardMarkup:
    """Клавиатура списка подписок: по кнопке 🗑 на каждую."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🗑 {s.filter.get('text', '(любые)')}",
            callback_data=SubDelCB(id=s.id).pack(),
        )]
        for s in subs
    ])


class SubscriptionIn(BaseModel):
    user_id: int
    text: str
    username: str | None = None


async def get_stack(session: AsyncSession, redis: Redis) -> list[dict]:
    """Топ технологий с кэшированием в Redis (cache-aside).

    Сначала пробуем взять из кэша; промах → считаем из БД, кладём в кэш с TTL.
    """
    cached = await redis.get(STACK_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)

    stats = await top_technologies(session)
    payload = [vars(s) for s in stats]
    await redis.set(STACK_CACHE_KEY, json.dumps(payload), ex=settings.stack_cache_ttl)
    return payload


def _format_vacancies(rows: list[VacancyRow]) -> str:
    if not rows:
        return "Ничего не найдено."
    lines = [
        f"• {v.name} — {v.employer}\n  {v.area} | {v.experience}\n  https://hh.ru/vacancy/{v.id}"
        for v in rows
    ]
    return "\n\n".join(lines)


class VacancyService:
    """Vacancy Radar: поиск вакансий (REST + команда бота)."""

    name = "vacancy"
    commands = [
        BotCommand(command="vacancies", description="поиск вакансий по разработке"),
        BotCommand(command="filter", description="подбор фильтров кнопками"),
        BotCommand(command="stack", description="топ востребованных технологий"),
        BotCommand(command="subscribe", description="подписка на новые вакансии"),
        BotCommand(command="subscriptions", description="мои подписки"),
        BotCommand(command="match", description="подбор вакансий по резюме (LLM)"),
    ]
    help_text = (
        "/vacancies [запрос] — поиск вакансий по разработке.\n"
        "  Запрос (необязательно) ищется в названии вакансии; "
        "без него — последние вакансии.\n"
        "  Пример: /vacancies senior python\n"
        "/filter — подобрать фильтры кнопками (регион/формат/аккредитация)\n"
        "/stack — топ востребованных технологий в dev-вакансиях\n"
        "/subscribe <запрос> — подписаться на новые вакансии по запросу\n"
        "/subscriptions — мои подписки\n"
        "/match — пришли резюме, подберу подходящие вакансии с пояснением"
    )

    def register(self, router: Router, infra: Infra) -> None:
        register_filter_dialog(router, infra)

        @router.message(Command("match"))
        async def match_start(message: Message, state: FSMContext) -> None:
            await state.set_state(MatchFlow.waiting_resume)
            await message.answer(
                "Пришли текст резюме (или ключевые навыки, стек, опыт) одним сообщением — "
                "подберу подходящие вакансии и поясню, почему.\n"
                "/cancel — отмена."
            )

        @router.message(Command("cancel"), MatchFlow.waiting_resume)
        async def match_cancel(message: Message, state: FSMContext) -> None:
            await state.clear()
            await message.answer("Подбор отменён.")

        @router.message(MatchFlow.waiting_resume)
        async def match_resume(message: Message, state: FSMContext) -> None:
            resume = (message.text or "").strip()
            if len(resume) < 20:
                await message.answer(
                    "Слишком коротко. Пришли текст резюме или хотя бы стек, опыт и роль."
                )
                return
            if not settings.llm_api_key:
                await state.clear()
                await message.answer("LLM не настроен: добавь LLM_API_KEY в .env")
                return
            # строгий лимит /match (дороже /ask: БД + LLM в фоне)
            allowed = await check_rate_limit(
                infra.redis, f"ratelimit:{message.from_user.id}:match",
                settings.match_rate_limit, settings.match_rate_limit_window,
            )
            if not allowed:
                await state.clear()
                await message.answer(
                    f"Лимит /match исчерпан ({settings.match_rate_limit} за "
                    f"{settings.match_rate_limit_window // 60} мин). Попробуй позже."
                )
                return
            # спенд-лимит: суточный бюджет токенов LLM (контроль стоимости)
            async with infra.session_factory() as session:
                if await over_budget(session, message.from_user.id, settings.llm_daily_token_budget):
                    await state.clear()
                    await message.answer(
                        "Дневной лимит токенов LLM исчерпан. Загляни в /usage; попробуй завтра."
                    )
                    return
            await state.clear()
            async with infra.session_factory() as session:
                await upsert_user(session, message.from_user.id, message.from_user.username)
                await session.commit()
            # тяжёлое → в Celery (бот не импортирует worker-код, шлёт по имени задачи)
            infra.celery.send_task(
                "services.vacancy.tasks.match_vacancies",
                args=[message.from_user.id, resume],
            )
            await message.answer("🔎 Подбираю вакансии по резюме — пришлю результат через ~минуту.")

        @router.message(Command("vacancies"))
        async def vacancies(message: Message) -> None:
            # текст после команды → поисковый запрос по названию
            query = message.text.partition(" ")[2].strip() or None
            async with infra.session_factory() as session:
                rows = await search_vacancies(
                    session, VacancyFilters(text=query, limit=10)
                )
            await message.answer(_format_vacancies(rows), disable_web_page_preview=True)

        @router.message(Command("stack"))
        async def stack(message: Message) -> None:
            async with infra.session_factory() as session:
                rows = await get_stack(session, infra.redis)
            top = rows[:20]
            lines = [f"{r['pct']}% — {r['technology']}" for r in top]
            await message.answer("Топ технологий (dev-вакансии):\n\n" + "\n".join(lines))

        @router.message(Command("subscribe"))
        async def subscribe(message: Message) -> None:
            query = message.text.partition(" ")[2].strip()
            if not query:
                await message.answer("Укажи запрос: /subscribe python")
                return
            async with infra.session_factory() as session:
                await upsert_user(
                    session, message.from_user.id, message.from_user.username
                )
                await create_subscription(
                    session, message.from_user.id, {"text": query}
                )
                await session.commit()
            await message.answer(f"Подписка создана: «{query}». Пришлю новые подходящие вакансии.")

        @router.message(Command("subscriptions"))
        async def subscriptions(message: Message) -> None:
            async with infra.session_factory() as session:
                subs = await list_user_subscriptions(session, message.from_user.id)
            if not subs:
                await message.answer("У тебя нет активных подписок. Создай: /subscribe python")
                return
            await message.answer("Твои подписки (тап 🗑 — удалить):", reply_markup=_subs_keyboard(subs))

        @router.callback_query(SubDelCB.filter())
        async def delete_sub(callback: CallbackQuery, callback_data: SubDelCB) -> None:
            async with infra.session_factory() as session:
                await deactivate_subscription(session, callback_data.id, callback.from_user.id)
                subs = await list_user_subscriptions(session, callback.from_user.id)
            if subs:
                await callback.message.edit_text(
                    "Твои подписки (тап 🗑 — удалить):", reply_markup=_subs_keyboard(subs)
                )
            else:
                await callback.message.edit_text("Подписок больше нет.")
            await callback.answer("Удалено")

    def api_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/vacancies")
        async def list_vacancies(
            text: str | None = None,
            area: str | None = None,
            work_format: str | None = None,
            accredited: bool | None = None,
            country: str | None = None,
            limit: int = 10,
            session: AsyncSession = Depends(get_session),
        ) -> list[dict]:
            rows = await search_vacancies(
                session,
                VacancyFilters(
                    text=text,
                    area=area,
                    work_format=work_format,
                    accredited=accredited,
                    country=country,
                    limit=limit,
                ),
            )
            return [vars(r) for r in rows]

        @router.get("/stack")
        async def stack(
            session: AsyncSession = Depends(get_session),
            redis: Redis = Depends(get_redis),
        ) -> list[dict]:
            return await get_stack(session, redis)

        @router.post("/subscriptions")
        async def create_sub(
            body: SubscriptionIn,
            session: AsyncSession = Depends(get_session),
        ) -> dict:
            await upsert_user(session, body.user_id, body.username)
            sub = await create_subscription(session, body.user_id, {"text": body.text})
            await session.commit()
            return {"id": sub.id, "user_id": body.user_id, "filter": sub.filter}

        @router.get("/subscriptions/{user_id}")
        async def list_subs(
            user_id: int, session: AsyncSession = Depends(get_session)
        ) -> list[dict]:
            subs = await list_user_subscriptions(session, user_id)
            return [{"id": s.id, "filter": s.filter} for s in subs]

        return router
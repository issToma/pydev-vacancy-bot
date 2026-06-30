from __future__ import annotations

import json

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.contracts import Infra
from core.db import get_session
from core.redis import get_redis
from services.vacancy.repo import (
    VacancyFilters,
    VacancyRow,
    search_vacancies,
    top_technologies,
)
from services.vacancy.subs import (
    create_subscription,
    list_user_subscriptions,
    upsert_user,
)

STACK_CACHE_KEY = "cache:stack"


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
        f"• {v.name} — {v.employer}\n  {v.area} | {v.experience}\n  {v.url}"
        for v in rows
    ]
    return "\n\n".join(lines)


class VacancyService:
    """Vacancy Radar: поиск вакансий (REST + команда бота)."""

    name = "vacancy"
    commands = ["/vacancies", "/stack", "/subscribe", "/subscriptions"]
    help_text = (
        "/vacancies [запрос] — поиск вакансий по разработке.\n"
        "  Запрос (необязательно) ищется в названии вакансии; "
        "без него — последние вакансии.\n"
        "  Пример: /vacancies senior python\n"
        "/stack — топ востребованных технологий в dev-вакансиях\n"
        "/subscribe <запрос> — подписаться на новые вакансии по запросу\n"
        "/subscriptions — мои подписки"
    )

    def register(self, router: Router, infra: Infra) -> None:
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
            lines = [f"• {s.filter.get('text', '(любые)')}" for s in subs]
            await message.answer("Твои подписки:\n\n" + "\n".join(lines))

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

        return router
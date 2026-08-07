from __future__ import annotations

import json
from typing import Annotated

from aiogram import F, Router
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
    count_all_vacancies,
    format_vacancy_card,
    salary_rows,
    search_vacancies,
    top_technologies,
)
from services.vacancy.fx import get_fx_rates
from services.vacancy.salary_stats import (
    GRADE_NON_DEV,
    GRADE_NONE,
    GRADE_OTHER,
    aggregate_by_grade,
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


class SearchFlow(StatesGroup):
    waiting_query = State()       # кнопка «Поиск» → ждём поисковый запрос


class SubscribeFlow(StatesGroup):
    waiting_query = State()       # кнопка «Подписаться» → ждём запрос подписки


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
    return "\n\n".join(format_vacancy_card(v) for v in rows)


def _fmt_rub(n: int) -> str:
    return f"{n:,}".replace(",", " ")          # 220000 → 220 000


def _fmt_rate(x: float) -> str:
    """Курс валюты с адаптивной точностью: у тенге/сума он сильно меньше 1."""
    if x >= 100:
        return f"{x:.0f}"
    if x >= 1:
        return f"{x:.1f}"
    return f"{x:.4f}".rstrip("0")              # 0.0064, 0.17, 0.9


class VacancyService:
    """Vacancy Radar: поиск/аналитика вакансий, подписки, подбор по резюме."""

    name = "vacancy"

    # ярлыки reply-кнопок раздела (их же обрабатываем ниже)
    BTN_SEARCH = "🔎 Поиск"
    BTN_FILTER = "🎛 Фильтры"
    BTN_STACK = "📊 Топ стек"
    BTN_SALARY = "💰 Зарплаты по грейдам"
    BTN_SUBSCRIBE = "🔔 Подписаться"
    BTN_SUBS = "📜 Мои подписки"
    BTN_MATCH = "🎯 Подбор по резюме"
    menu_buttons = [BTN_SEARCH, BTN_FILTER, BTN_STACK, BTN_SALARY, BTN_SUBSCRIBE, BTN_SUBS, BTN_MATCH]

    # команды (работают по вводу; в /-меню не показываем — навигация кнопками)
    commands = [
        BotCommand(command="vacancies", description="поиск вакансий по разработке"),
        BotCommand(command="filter", description="подбор фильтров кнопками"),
        BotCommand(command="stack", description="топ востребованных технологий"),
        BotCommand(command="salaries", description="средняя/медианная ЗП по грейдам"),
        BotCommand(command="subscribe", description="подписка на новые вакансии"),
        BotCommand(command="subscriptions", description="мои подписки"),
        BotCommand(command="match", description="подбор вакансий по резюме (LLM)"),
    ]
    help_text = (
        "🔎 Поиск — вакансии по названию · 🎛 Фильтры — регион/формат/аккредитация\n"
        "📊 Топ стек — востребованные технологии · 💰 Зарплаты — средняя/медиана по грейдам\n"
        "🔔 Подписаться — уведомления о новых · 📜 Мои подписки — список/удаление\n"
        "🎯 Подбор по резюме — подбор через LLM"
    )

    def register(self, router: Router, infra: Infra) -> None:
        register_filter_dialog(router, infra, self.BTN_FILTER)

        # ── общая логика (переиспользуется командой и кнопкой) ──
        async def _show_vacancies(message: Message, query: str | None) -> None:
            async with infra.session_factory() as session:
                rows = await search_vacancies(session, VacancyFilters(text=query, limit=10))
            if not rows:
                await message.answer("Ничего не найдено, попробуй другой запрос")
                return
            # показываем первые 10 (по свежести)
            await message.answer(
                _format_vacancies(rows), parse_mode="HTML", disable_web_page_preview=True
            )

        async def _show_stack(message: Message) -> None:
            async with infra.session_factory() as session:
                rows = await get_stack(session, infra.redis)
            lines = [f"{r['pct']}% — {r['technology']}" for r in rows[:20]]
            await message.answer("Топ технологий (dev-вакансии):\n\n" + "\n".join(lines))

        async def _show_salaries(message: Message) -> None:
            rates = await get_fx_rates(infra.redis)
            async with infra.session_factory() as session:
                rows = await salary_rows(session)
                total = await count_all_vacancies(session)
            stats = aggregate_by_grade(rows, rates)
            if not stats:
                await message.answer("Нет вакансий с указанной зарплатой")
                return
            grades = {s.grade for s in stats}
            lines = [
                "<b>💰 Python — зарплаты по грейдам</b>, в рублях",
                "<i>грейд определяется по названию вакансии</i>",
            ]
            for s in stats:
                lines.append("")
                lines.append(f"<b>{s.grade}</b> · {s.count}")
                lines.append(
                    f"средняя <b>{_fmt_rub(s.mean_rub)}</b> · "
                    f"медиана <b>{_fmt_rub(s.median_rub)}</b>"
                )
                lines.append(
                    f"вилка <b>{_fmt_rub(s.band_lo_rub)}</b> – "
                    f"<b>{_fmt_rub(s.band_hi_rub)}</b> <i>(½ рынка)</i>"
                )

            # служебный подвал — в цитату-блок, разбитый на подписанные мини-секции
            # (покрытие / курс / легенда), чтобы не сливалось в мешанину.
            counted = sum(s.count for s in stats)
            note = ["<b>Покрытие</b>", f"учтено {counted} из {total} · без ЗП {total - len(rows)}"]

            # курс показываем для валют, что реально встречаются в данных (не только $/€);
            # рубли и валюты без курса ЦБ не показываем. USD/EUR — вперёд, дальше по алфавиту.
            present = sorted(
                {(r[3] or "RUR").upper() for r in rows} - {"RUR", "RUB"},
                key=lambda c: (c not in ("USD", "EUR"), c),
            )
            fx = [f"{c} {_fmt_rate(rates[c])}" for c in present if c in rates]
            if fx:
                iso = (rates.get("_as_of") or "")[:10]
                as_of = ".".join(reversed(iso.split("-"))) if iso else ""   # 2026-07-03 → 03.07.2026
                note += ["", "<b>Курс ЦБ</b>" + (f" · {as_of}" if as_of else ""), " · ".join(fx)]

            legend = []
            if GRADE_OTHER in grades:
                legend.append(f"{GRADE_OTHER} — тимлид, хед, архитектор, principal")
            if GRADE_NONE in grades:
                legend.append(f"{GRADE_NONE} — нет junior/middle/senior в названии")
            if GRADE_NON_DEV in grades:
                legend.append(f"{GRADE_NON_DEV} — QA, аналитики, Data/ML/DevOps и пр. (по роли)")
            if legend:
                note += ["", "<b>Обозначения</b>", *legend]

            lines.append("")
            lines.append("<blockquote>" + "\n".join(note) + "</blockquote>")

            await message.answer("\n".join(lines), parse_mode="HTML")

        async def _create_sub(message: Message, query: str) -> None:
            async with infra.session_factory() as session:
                await upsert_user(session, message.from_user.id, message.from_user.username)
                await create_subscription(session, message.from_user.id, {"text": query})
                await session.commit()
            await message.answer(f"Подписка создана: «{query}». Пришлю новые подходящие вакансии.")

        async def _show_subs(message: Message) -> None:
            async with infra.session_factory() as session:
                subs = await list_user_subscriptions(session, message.from_user.id)
            if not subs:
                await message.answer("У тебя нет активных подписок. Нажми «🔔 Подписаться».")
                return
            await message.answer("Твои подписки (тап 🗑 — удалить):", reply_markup=_subs_keyboard(subs))

        async def _start_match(message: Message, state: FSMContext) -> None:
            await state.set_state(MatchFlow.waiting_resume)
            await message.answer(
                "Пришли текст резюме одним сообщением, подберу подходящие вакансии и поясню почему"
            )

        # ── команды (по вводу) ──
        @router.message(Command("vacancies"))
        async def cmd_vacancies(message: Message) -> None:
            await _show_vacancies(message, message.text.partition(" ")[2].strip() or None)

        @router.message(Command("stack"))
        async def cmd_stack(message: Message) -> None:
            await _show_stack(message)

        @router.message(Command("salaries"))
        async def cmd_salaries(message: Message) -> None:
            await _show_salaries(message)

        @router.message(Command("subscribe"))
        async def cmd_subscribe(message: Message) -> None:
            query = message.text.partition(" ")[2].strip()
            if not query:
                await message.answer("Укажи запрос: /subscribe python")
                return
            await _create_sub(message, query)

        @router.message(Command("subscriptions"))
        async def cmd_subs(message: Message) -> None:
            await _show_subs(message)

        @router.message(Command("match"))
        async def cmd_match(message: Message, state: FSMContext) -> None:
            await _start_match(message, state)

        # ── кнопки-слова раздела (регистрируем ДО prompt-обработчиков состояний) ──
        @router.message(F.text == self.BTN_SEARCH)
        async def btn_search(message: Message, state: FSMContext) -> None:
            await state.set_state(SearchFlow.waiting_query)
            await message.answer("Напиши запрос — ищу по названию вакансии")

        @router.message(F.text == self.BTN_STACK)
        async def btn_stack(message: Message, state: FSMContext) -> None:
            await state.clear()
            await _show_stack(message)

        @router.message(F.text == self.BTN_SALARY)
        async def btn_salaries(message: Message, state: FSMContext) -> None:
            await state.clear()
            await _show_salaries(message)

        @router.message(F.text == self.BTN_SUBSCRIBE)
        async def btn_subscribe(message: Message, state: FSMContext) -> None:
            await state.set_state(SubscribeFlow.waiting_query)
            await message.answer("На какой запрос подписать")

        @router.message(F.text == self.BTN_SUBS)
        async def btn_subs(message: Message, state: FSMContext) -> None:
            await state.clear()
            await _show_subs(message)

        @router.message(F.text == self.BTN_MATCH)
        async def btn_match(message: Message, state: FSMContext) -> None:
            await _start_match(message, state)

        # ── ввод для prompt-флоу (после кнопок: свободный текст сюда, ярлыки — в кнопки) ──
        @router.message(SearchFlow.waiting_query)
        async def on_search_query(message: Message, state: FSMContext) -> None:
            # состояние НЕ сбрасываем — остаёмся в поиске, ждём следующий запрос
            # (выйти можно кнопкой раздела или «☰ Меню»)
            await _show_vacancies(message, (message.text or "").strip() or None)

        @router.message(SubscribeFlow.waiting_query)
        async def on_subscribe_query(message: Message, state: FSMContext) -> None:
            # остаёмся в подписке (можно добавить ещё); выход кнопкой или «☰ Меню»
            query = (message.text or "").strip()
            if not query:
                await message.answer("Пустой запрос, подписка не создана")
                return
            await _create_sub(message, query)

        @router.message(MatchFlow.waiting_resume)
        async def on_resume(message: Message, state: FSMContext) -> None:
            # состояние НЕ сбрасываем — остаёмся в подборе; выход кнопкой или «☰ Меню»
            resume = (message.text or "").strip()
            if len(resume) < 20:
                await message.answer("Слишком коротко, пришли текст резюме")
                return
            if not settings.llm_api_key:
                await message.answer("LLM не настроен, добавь LLM_API_KEY в .env")
                return
            allowed = await check_rate_limit(
                infra.redis, f"ratelimit:{message.from_user.id}:match",
                settings.match_rate_limit, settings.match_rate_limit_window,
            )
            if not allowed:
                await message.answer(
                    f"Лимит подбора исчерпан, {settings.match_rate_limit} за "
                    f"{settings.match_rate_limit_window // 60} мин, попробуй позже"
                )
                return
            async with infra.session_factory() as session:
                if await over_budget(session, message.from_user.id, settings.llm_daily_token_budget):
                    await message.answer("Дневной лимит токенов LLM исчерпан, попробуй завтра")
                    return
            async with infra.session_factory() as session:
                await upsert_user(session, message.from_user.id, message.from_user.username)
                await session.commit()
            infra.celery.send_task(
                "services.vacancy.tasks.match_vacancies",
                args=[message.from_user.id, resume],
            )
            await message.answer("Подбираю вакансии по резюме, пришлю через минуту")

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

        @router.get("/vacancies", tags=["Вакансии"])
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

        @router.get("/stack", tags=["Аналитика"])
        async def stack(
            session: AsyncSession = Depends(get_session),
            redis: Redis = Depends(get_redis),
        ) -> list[dict]:
            return await get_stack(session, redis)

        # Depends() на Pydantic-модели разворачивает её поля в отдельные query-
        # параметры (в Swagger — раздельные инпуты), а не один JSON-body.
        @router.post("/subscriptions", tags=["Подписки"])
        async def create_sub(
            body: Annotated[SubscriptionIn, Depends()],
            session: AsyncSession = Depends(get_session),
        ) -> dict:
            await upsert_user(session, body.user_id, body.username)
            sub = await create_subscription(session, body.user_id, {"text": body.text})
            await session.commit()
            return {"id": sub.id, "user_id": body.user_id, "filter": sub.filter}

        @router.get("/subscriptions/{user_id}", tags=["Подписки"])
        async def list_subs(
            user_id: int, session: AsyncSession = Depends(get_session)
        ) -> list[dict]:
            subs = await list_user_subscriptions(session, user_id)
            return [{"id": s.id, "filter": s.filter} for s in subs]

        return router
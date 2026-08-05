from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import redis.asyncio as aioredis
from sqlalchemy import NullPool, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.celery_app import celery_app
from core.config import settings
from core.models import Job, LlmUsage
from services.vacancy.match import extract_area, extract_stack, prefilter_candidates, rank_with_llm
from services.vacancy.models import VacancyScraped
from services.vacancy.notifications import publish_notifications
from services.vacancy.scraper.hh_scraper import scrape_new_vacancies
from services.vacancy.subs import list_active_subscriptions, subscription_matches

TELEGRAM_TEXT_LIMIT = 4096

logger = logging.getLogger(__name__)

# Поля, которыми владеет приложение, — скрапер их НЕ выставляет и НЕ перезаписывает.
APP_OWNED = {"vacancy_status", "apply_status"}

# Single-flight lock: не запускаем второй скрап, пока идёт первый (наложение расписаний Beat).
SCRAPE_LOCK_KEY = "lock:scrape_vacancies"
# TTL > жёсткого task_time_limit (600с) — если задачу убили по лимиту (finally не
# выполнится), лок сам снимется и не заблокирует будущие прогоны навсегда.
SCRAPE_LOCK_TTL = 900


async def _upsert_batch(session_factory: async_sessionmaker, batch: list[dict]) -> None:
    """Записать батч вакансий: новые — INSERT, существующие — UPDATE данных,
    КРОМЕ полей приложения (vacancy_status/apply_status). updated_at бампается.
    """
    # Скрапер кладёт vacancy_status='available' — убираем: этими полями владеет приложение.
    rows = [{k: v for k, v in row.items() if k not in APP_OWNED} for row in batch]
    # Один UPSERT не может обновить одну целевую строку дважды (CardinalityViolationError),
    # поэтому страхуемся от дублей по id внутри батча — последнее вхождение побеждает.
    rows = list({row["id"]: row for row in rows}.values())

    stmt = pg_insert(VacancyScraped).values(rows)
    # Обновляем ТОЛЬКО реально вставляемые колонки (ключи rows), кроме PK.
    # vacancy_status/apply_status в rows отсутствуют (APP_OWNED) → в UPDATE не попадут.
    # Берём значения из «виртуальной» строки excluded (то, что пытались вставить).
    insert_keys = rows[0].keys()
    update_cols = {key: stmt.excluded[key] for key in insert_keys if key != "id"}
    update_cols["updated_at"] = func.now()

    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)

    async with session_factory() as session:
        await session.execute(stmt)
        await session.commit()


async def _run() -> int:
    """Async-ядро задачи: собрать новые вакансии и записать батчами в БД."""
    # Отдельный движок на запуск задачи: в Celery (prefork) у каждого вызова свой
    # event loop (asyncio.run), а asyncpg-соединения привязаны к loop. Свой engine
    # с NullPool исключает переиспользование соединений между разными loop'ами.
    # Берём лок: SET key NX EX=ttl. Не взяли → предыдущий скрап ещё идёт, пропускаем.
    redis = aioredis.from_url(settings.redis_url)
    got_lock = await redis.set(SCRAPE_LOCK_KEY, "1", nx=True, ex=SCRAPE_LOCK_TTL)
    if not got_lock:
        logger.info("scrape_vacancies уже идёт — пропускаю этот прогон")
        await redis.aclose()
        return 0

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    saved = 0
    try:
        # активные подписки грузим один раз — матчим против них новые вакансии
        async with session_factory() as session:
            existing_ids = set((await session.execute(select(VacancyScraped.id))).scalars().all())
            subscriptions = await list_active_subscriptions(session)
        logger.info("Уже в БД: %s вакансий, активных подписок: %s", len(existing_ids), len(subscriptions))

        async def on_batch(batch: list[dict]) -> None:
            nonlocal saved
            await _upsert_batch(session_factory, batch)
            saved += len(batch)

            # уведомления только по НОВЫМ вакансиям (id не было в БД до запуска)
            notifications = []
            for vacancy in batch:
                if vacancy["id"] in existing_ids:
                    continue
                for sub in subscriptions:
                    if subscription_matches(sub.filter, vacancy):
                        notifications.append({
                            "user_id": sub.user_id,
                            # человекочитаемая страница вакансии (api.hh.ru без регистрации пуст)
                            "text": f"Новая вакансия по подписке «{sub.filter.get('text', '')}»:\n"
                                    f"{vacancy['name']}\nhttps://hh.ru/vacancy/{vacancy['id']}",
                        })
            await publish_notifications(notifications)

        await scrape_new_vacancies(existing_ids, on_batch=on_batch)
    finally:
        await engine.dispose()
        await redis.delete(SCRAPE_LOCK_KEY)     # ранний отпуск лока при штатном завершении
        await redis.aclose()

    return saved


@celery_app.task(name="services.vacancy.tasks.scrape_vacancies")
def scrape_vacancies() -> int:
    """Celery-задача: скрапинг вакансий с hh.ru → upsert в vacancies_scraped.

    Задача синхронная (так устроен воркер Celery), а скрапер асинхронный —
    мостимся через asyncio.run, который создаёт event loop на время задачи.
    Возвращает число сохранённых/обновлённых вакансий.
    """
    saved = asyncio.run(_run())
    logger.info("scrape_vacancies: сохранено/обновлено %s", saved)
    return saved


# ─────────────────────────── /match (подбор по резюме) ───────────────────────────

async def _run_match(user_id: int, resume: str) -> str:
    """Async-ядро подбора: предфильтр в БД → LLM-ранжирование → job + учёт + уведомление."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    job_id = uuid4()
    try:
        # заводим job в статусе running (история задач)
        async with session_factory() as session:
            session.add(Job(
                id=job_id, user_id=user_id, service="vacancy", kind="match",
                status="running", params={"resume_chars": len(resume)},
            ))
            await session.commit()

        try:
            # 1) дешёвый предфильтр кандидатов по стеку (+ городу, если упомянут) из резюме
            stack = extract_stack(resume)
            async with session_factory() as session:
                area = await extract_area(session, resume)
                candidates = await prefilter_candidates(
                    session, stack, settings.match_candidates_limit, area=area
                )
            logger.info("match: стек=%s, город=%s, кандидатов=%s", stack[:10], area, len(candidates))

            # 2) LLM ранжирует ТОЛЬКО кандидатов (не всю базу)
            if candidates:
                answer, usage = await rank_with_llm(resume, candidates)
            else:
                answer, usage = "По вашему стеку подходящих вакансий не нашлось.", None

            # 3) job → done + учёт токенов
            async with session_factory() as session:
                job = await session.get(Job, job_id)
                job.status = "done"
                job.result_ref = answer[:TELEGRAM_TEXT_LIMIT]
                if usage is not None:
                    session.add(LlmUsage(
                        user_id=user_id, service="match", model=settings.llm_model,
                        tokens_in=usage.prompt_tokens or 0, tokens_out=usage.completion_tokens or 0,
                    ))
                await session.commit()

            # 4) доставка результата пользователю (тот же RabbitMQ-путь)
            header = f"🔎 Подбор по резюме (кандидатов: {len(candidates)}):\n\n"
            await publish_notifications([{
                "user_id": user_id,
                "text": (header + answer)[:TELEGRAM_TEXT_LIMIT],
            }])
            return "ok"
        except Exception as e:
            logger.exception("match: ошибка ранжирования")
            async with session_factory() as session:
                job = await session.get(Job, job_id)
                if job:
                    job.status = "failed"
                    job.result_ref = str(e)[:500]
                    await session.commit()
            await publish_notifications([{
                "user_id": user_id,
                "text": "⚠️ Не удалось выполнить подбор по резюме. Попробуйте позже.",
            }])
            return "failed"
    finally:
        await engine.dispose()


@celery_app.task(name="services.vacancy.tasks.match_vacancies")
def match_vacancies(user_id: int, resume: str) -> str:
    """Celery-задача: подбор вакансий по резюме (тяжёлое → фон, в отличие от /ask).

    Двухэтапно: SQL-предфильтр кандидатов → LLM-ранжирование топ-N с пояснением.
    Результат кладётся в jobs.result_ref и доставляется пользователю уведомлением.
    """
    return asyncio.run(_run_match(user_id, resume))
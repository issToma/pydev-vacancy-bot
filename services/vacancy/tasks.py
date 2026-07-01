from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis
from sqlalchemy import NullPool, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.celery_app import celery_app
from core.config import settings
from services.vacancy.models import VacancyScraped
from services.vacancy.notifications import publish_notifications
from services.vacancy.scraper.hh_scraper import scrape_new_vacancies
from services.vacancy.subs import list_active_subscriptions, subscription_matches

logger = logging.getLogger(__name__)

# Поля, которыми владеет приложение, — скрапер их НЕ выставляет и НЕ перезаписывает.
APP_OWNED = {"status", "apply_flag"}

# Single-flight lock: не запускаем второй скрап, пока идёт первый (наложение расписаний Beat).
SCRAPE_LOCK_KEY = "lock:scrape_vacancies"
# TTL > жёсткого task_time_limit (600с) — если задачу убили по лимиту (finally не
# выполнится), лок сам снимется и не заблокирует будущие прогоны навсегда.
SCRAPE_LOCK_TTL = 900


async def _upsert_batch(session_factory: async_sessionmaker, batch: list[dict]) -> None:
    """Записать батч вакансий: новые — INSERT, существующие — UPDATE данных,
    КРОМЕ полей приложения (status/apply_flag). updated_at при обновлении бампается.
    """
    # Скрапер кладёт status='available' в каждую строку — убираем: статус ведёт приложение.
    rows = [{k: v for k, v in row.items() if k not in APP_OWNED} for row in batch]

    stmt = pg_insert(VacancyScraped).values(rows)
    # Обновляем ТОЛЬКО реально вставляемые колонки (ключи rows), кроме PK.
    # status/apply_flag в rows отсутствуют (APP_OWNED) → в UPDATE не попадут.
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
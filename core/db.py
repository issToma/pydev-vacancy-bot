from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from core.config import settings


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей: от него наследуются все таблицы.

    Base.metadata собирает схему всех импортированных моделей —
    это использует Alembic для autogenerate миграций.
    """


# Асинхронный движок (драйвер asyncpg). echo=True временно включают для отладки SQL.
async_engine = create_async_engine(settings.database_url, echo=False)

# Фабрика асинхронных сессий. expire_on_commit=False — объекты остаются
# доступны после commit (без неожиданных ленивых обращений к БД в async-коде).
async_session_factory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: сессия БД на запрос (открывается и закрывается per-request)."""
    async with async_session_factory() as session:
        yield session
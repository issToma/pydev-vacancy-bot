from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings, settings
from core.db import async_session_factory
from core.contracts import Infra


@dataclass(frozen=True)
class AppInfra:
    """Конкретная реализация Infra. Структурно удовлетворяет протоколу Infra.

    По мере шагов добавим сюда redis, celery, s3, claude.
    """

    settings: Settings
    session_factory: async_sessionmaker


def build_infra() -> Infra:
    """Собрать инфраструктуру приложения (единая точка инициализации зависимостей)."""
    return AppInfra(settings=settings, session_factory=async_session_factory)
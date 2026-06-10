from __future__ import annotations

from dataclasses import dataclass

from core.config import Settings, settings
from core.contracts import Infra


@dataclass(frozen=True)
class AppInfra:
    """Конкретная реализация Infra. Структурно удовлетворяет протоколу Infra.

    Пока держит только settings. По мере шагов добавим сюда
    session_factory (БД), redis, celery, s3, claude.
    """

    settings: Settings


def build_infra() -> Infra:
    """Собрать инфраструктуру приложения (единая точка инициализации зависимостей)."""
    return AppInfra(settings=settings)
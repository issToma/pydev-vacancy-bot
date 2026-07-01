from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings, settings
from core.db import async_session_factory
from core.contracts import Infra
from core.redis import redis_client
from core.llm import llm_client
from core import s3 as s3_module
from core.celery_app import celery_app


@dataclass(frozen=True)
class AppInfra:
    """Конкретная реализация Infra. Структурно удовлетворяет протоколу Infra."""

    settings: Settings
    session_factory: async_sessionmaker
    redis: Redis
    s3: object          # модуль core.s3 (put_object/get_object/ensure_buckets)
    celery: object      # Celery-приложение (send_task)
    llm: AsyncOpenAI    # OpenAI-совместимый LLM-клиент (для /ask)


def build_infra() -> Infra:
    """Собрать инфраструктуру приложения (единая точка инициализации зависимостей)."""
    return AppInfra(
        settings=settings,
        session_factory=async_session_factory,
        redis=redis_client,
        s3=s3_module,
        celery=celery_app,
        llm=llm_client,
    )
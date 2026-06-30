from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aiogram import Router
from aiogram.types import BotCommand

if TYPE_CHECKING:
    from fastapi import APIRouter
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from core.config import Settings


class Infra(Protocol):
    """Общая инфраструктура, которую ядро выдаёт каждому сервису (Dependency Injection).

    По мере добавления инфраструктуры (Celery, S3, Claude)
    расширяем этот протокол — сервисы берут зависимости отсюда,
    а не импортируют глобально.
    """

    settings: Settings
    session_factory: async_sessionmaker
    redis: Redis
    s3: object          # модуль core.s3 (put_object/get_object/ensure_buckets)


class ServiceModule(Protocol):
    """Контракт сервиса-плагина.

    Новый сервис = класс, реализующий этот протокол, + одна строка
    в bot/registry.SERVICES. Ядро бота и инфраструктура при этом НЕ меняются
    (принцип open/closed).
    """

    name: str
    commands: list[BotCommand]   # команды сервиса с описаниями (для меню Telegram)
    help_text: str

    def register(self, router: Router, infra: Infra) -> None:
        """Зарегистрировать aiogram-хендлеры сервиса на переданном роутере.

        Вызывается один раз при старте. Синхронный: регистрация хендлеров
        не делает I/O — только навешивает обработчики на router.
        """
        ...

    def api_router(self) -> APIRouter | None:
        """Вернуть FastAPI-роутер сервиса (или None, если REST-эндпоинтов нет).

        Без аргументов: сервис сам строит и возвращает свой APIRouter.
        """
        ...

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aiogram import Router

if TYPE_CHECKING:
    from fastapi import APIRouter

    from core.config import Settings


class Infra(Protocol):
    """Общая инфраструктура, которую ядро выдаёт каждому сервису (Dependency Injection).

    Пока здесь только settings. По мере добавления инфраструктуры
    (БД, Redis, Celery, S3, Claude) расширяем этот протокол — сервисы
    будут брать зависимости отсюда, а не импортировать глобально.
    """

    settings: Settings


class ServiceModule(Protocol):
    """Контракт сервиса-плагина.

    Новый сервис = класс, реализующий этот протокол, + одна строка
    в bot/registry.SERVICES. Ядро бота и инфраструктура при этом НЕ меняются
    (принцип open/closed).
    """

    name: str
    commands: list[str]
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

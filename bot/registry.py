from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from core.contracts import ServiceModule
from services.ask.service import AskService
from services.dewarp.service import DewarpService
from services.ping import PingService
from services.vacancy.service import VacancyService

# Единственная точка, которую правят при добавлении сервиса: +1 строка сюда.
SERVICES: list[ServiceModule] = [
    PingService(),
    VacancyService(),
    DewarpService(),
    AskService(),
]


def _duplicates(items: Iterable[str]) -> list[str]:
    """Значения, встречающиеся во входной последовательности более одного раза."""
    return [item for item, count in Counter(items).items() if count > 1]


def validate_registry(services: list[ServiceModule]) -> None:
    """Проверить инварианты реестра на старте (fail-fast).

    Падает с ValueError, если имена сервисов не уникальны либо команды
    пересекаются между сервисами — иначе роутинг был бы неоднозначным.
    """
    duplicate_names = _duplicates(s.name for s in services)
    if duplicate_names:
        raise ValueError(f"Дублируются имена сервисов: {duplicate_names}")

    all_commands = (cmd.command for s in services for cmd in s.commands)
    duplicate_commands = _duplicates(all_commands)
    if duplicate_commands:
        raise ValueError(f"Команды используются более чем одним сервисом: {duplicate_commands}")
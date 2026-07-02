from types import SimpleNamespace

import pytest
from aiogram.types import BotCommand

from bot.registry import SERVICES, validate_registry


def _svc(name: str, cmds: list[str]):
    return SimpleNamespace(
        name=name,
        commands=[BotCommand(command=c, description="x") for c in cmds],
    )


def test_real_registry_is_valid():
    # реальный реестр проекта не должен нарушать инварианты
    validate_registry(SERVICES)


def test_duplicate_service_name_raises():
    with pytest.raises(ValueError):
        validate_registry([_svc("a", ["x"]), _svc("a", ["y"])])


def test_duplicate_command_across_services_raises():
    with pytest.raises(ValueError):
        validate_registry([_svc("a", ["dup"]), _svc("b", ["dup"])])

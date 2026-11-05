"""Навигация и состав раздела.

Ask сращён с сервисом вакансий (services/vacancy/ask.py), отдельным ServiceModule
он больше не является. Тесты следят за тем, что его кнопки, команды и справка не
потерялись при сращивании, и что на клавиатуре нет кнопок-пустышек.
"""
from bot.navigation import MENU_LABEL, SECTION_LABELS, _main_kb, _section_kb
from bot.registry import SERVICES
from services.vacancy.ask import ASK_BUTTONS, ASK_COMMANDS, ASK_HELP

VACANCY = next(s for s in SERVICES if s.name == "vacancy")


def test_single_service_registry() -> None:
    assert [s.name for s in SERVICES] == ["vacancy"], "сервис в проекте один"
    assert set(SECTION_LABELS) == {"vacancy"}


def test_ask_is_part_of_vacancy_service() -> None:
    """То, ради чего сращивали: Ask доступен из раздела вакансий."""
    assert set(ASK_BUTTONS) <= set(VACANCY.menu_buttons)
    commands = {c.command for c in VACANCY.commands}
    assert {c.command for c in ASK_COMMANDS} <= commands


def test_help_mentions_every_button() -> None:
    """Кнопка без строки в справке — признак, что её забыли описать."""
    for label in VACANCY.menu_buttons:
        assert label in VACANCY.help_text, f"кнопка «{label}» не описана в справке"
    assert ASK_HELP in VACANCY.help_text


def test_buttons_are_unique() -> None:
    """Повтор ярлыка = два хендлера на один текст, сработает случайный."""
    labels = VACANCY.menu_buttons
    assert len(labels) == len(set(labels))


def test_section_keyboard_layout() -> None:
    rows = _section_kb(VACANCY).keyboard
    flat = [b.text for row in rows for b in row]
    assert flat[:-1] == list(VACANCY.menu_buttons), "кнопки раздела идут как объявлены"
    assert flat[-1] == MENU_LABEL, "последняя кнопка — выход/сброс"
    assert all(len(row) <= 2 for row in rows), "по две кнопки в ряд"


def test_main_keyboard_has_every_section() -> None:
    labels = {b.text for row in _main_kb(SERVICES).keyboard for b in row}
    assert labels == set(SECTION_LABELS.values())

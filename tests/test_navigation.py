"""Навигация: состав разделов согласован с реестром сервисов.

Ask живёт кнопкой внутри раздела вакансий, а не отдельным разделом, поэтому
состав кнопок знает навигация. Легко опечататься в имени сервиса или оставить на
клавиатуре кнопку, которую никто не обрабатывает — тесты ровно про это.
"""
from bot.navigation import (
    MENU_LABEL,
    SECTION_LABELS,
    SECTION_SERVICES,
    _section_kb,
    _section_labels,
)
from bot.registry import SERVICES

NAMES = {s.name for s in SERVICES}


def test_sections_reference_existing_services() -> None:
    assert set(SECTION_LABELS) <= NAMES, "раздел ссылается на несуществующий сервис"
    assert set(SECTION_SERVICES) <= NAMES
    for section, members in SECTION_SERVICES.items():
        assert set(members) <= NAMES, f"раздел {section}: неизвестные сервисы"


def test_every_button_belongs_to_some_service() -> None:
    """Кнопка, которой нет ни у одного сервиса, на нажатие ничего не сделает."""
    known = {label for s in SERVICES for label in s.menu_buttons}
    for section in SECTION_LABELS:
        for label in _section_labels(section, SERVICES):
            assert label in known, f"кнопку «{label}» никто не обрабатывает"


def test_labels_are_unique_within_section() -> None:
    """Две одинаковые кнопки = два хендлера на один текст, сработает случайный."""
    for section in SECTION_LABELS:
        labels = _section_labels(section, SERVICES)
        assert len(labels) == len(set(labels)), f"раздел {section}: повторы кнопок"


def test_ask_buttons_are_inside_vacancy_section() -> None:
    """Собственно то, ради чего всё это: Ask доступен из раздела вакансий."""
    labels = _section_labels("vacancy", SERVICES)
    ask = next(s for s in SERVICES if s.name == "ask")
    assert set(ask.menu_buttons) <= set(labels)
    assert "ask" not in SECTION_LABELS, "Ask не должен быть отдельным разделом"


def test_keyboard_has_exit_button() -> None:
    rows = _section_kb("vacancy", SERVICES).keyboard
    assert rows[-1][0].text == MENU_LABEL, "последняя кнопка — выход/сброс"
    assert all(len(row) <= 2 for row in rows), "по две кнопки в ряд"

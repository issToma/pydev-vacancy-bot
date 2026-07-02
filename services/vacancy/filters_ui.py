from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.contracts import Infra
from services.vacancy.repo import VacancyFilters, search_vacancies


class FilterFlow(StatesGroup):
    building = State()      # пользователь собирает фильтры кнопками


class FilterCB(CallbackData, prefix="flt"):
    """Структурированные callback_data кнопок конструктора фильтров."""
    action: str             # country | format | accredited | search
    value: str = ""         # ru/foreign/any | remote/any


def _mark(active: bool, label: str) -> str:
    return ("✅ " if active else "") + label


def build_filter_kb(data: dict) -> InlineKeyboardMarkup:
    """Клавиатура конструктора фильтров (data из FSM).

    Каждая опция — переключатель: тап по активной снимает её.
    Ничего не выбрано = «любой» (фильтр не применяется).
    """
    country = data.get("country")
    work_format = data.get("work_format")
    accredited = data.get("accredited")

    rows = [
        [
            InlineKeyboardButton(text=_mark(country == "ru", "РФ"),
                                 callback_data=FilterCB(action="country", value="ru").pack()),
            InlineKeyboardButton(text=_mark(country == "foreign", "Зарубеж"),
                                 callback_data=FilterCB(action="country", value="foreign").pack()),
        ],
        [
            InlineKeyboardButton(text=_mark(work_format == "Удалённо", "Удалёнка"),
                                 callback_data=FilterCB(action="format", value="remote").pack()),
        ],
        [
            InlineKeyboardButton(text=_mark(bool(accredited), "Только аккредитованные IT"),
                                 callback_data=FilterCB(action="accredited").pack()),
        ],
        [
            InlineKeyboardButton(text="🔎 Искать",
                                 callback_data=FilterCB(action="search").pack()),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def register_filter_dialog(router: Router, infra: Infra, button_label: str | None = None) -> None:
    async def open_filter(message: Message, state: FSMContext) -> None:
        await state.set_state(FilterFlow.building)
        await state.set_data({})
        await message.answer(
            "Отметь фильтры и запусти поиск",
            reply_markup=build_filter_kb({}),
        )

    router.message(Command("filter"))(open_filter)
    if button_label:                       # ярлык reply-кнопки раздела → тот же диалог
        router.message(F.text == button_label)(open_filter)

    @router.callback_query(FilterCB.filter(), FilterFlow.building)
    async def on_toggle(callback: CallbackQuery, callback_data: FilterCB, state: FSMContext) -> None:
        data = await state.get_data()

        if callback_data.action == "country":
            # тап по активному региону снимает его, иначе выбирает
            data["country"] = None if data.get("country") == callback_data.value else callback_data.value
        elif callback_data.action == "format":
            data["work_format"] = None if data.get("work_format") else "Удалённо"
        elif callback_data.action == "accredited":
            data["accredited"] = not data.get("accredited")      # toggle
        elif callback_data.action == "search":
            await _run_search(callback, data, infra)
            return

        await state.set_data(data)
        try:
            await callback.message.edit_reply_markup(reply_markup=build_filter_kb(data))
        except TelegramBadRequest:
            # повторный тап по уже выбранной опции → клавиатура не изменилась — это ок
            pass
        await callback.answer()


async def _run_search(callback: CallbackQuery, data: dict, infra: Infra) -> None:
    async with infra.session_factory() as session:
        rows = await search_vacancies(
            session,
            VacancyFilters(
                country=data.get("country"),
                work_format=data.get("work_format"),
                accredited=data.get("accredited") or None,
                limit=10,
            ),
        )
    if rows:
        text = "\n\n".join(
            f"• {v.name} — {v.employer}\n  {v.area} | https://hh.ru/vacancy/{v.id}" for v in rows
        )
    else:
        text = "Ничего не найдено по выбранным фильтрам"
    # у прежней клавиатуры убираем кнопки — активной остаётся ОДНА (новая, снизу),
    # чтобы не было двух рабочих клавиатур с рассинхроном галочек
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(text, disable_web_page_preview=True)
    # клавиатуру фильтров показываем ПОД результатами (с текущими галочками) —
    # не нужно скроллить вверх; состояние НЕ сбрасываем, можно менять и искать снова
    await callback.message.answer("Изменить фильтры", reply_markup=build_filter_kb(data))
    await callback.answer()
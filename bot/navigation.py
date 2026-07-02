from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from core.contracts import ServiceModule

MENU_LABEL = "☰ Меню"
MENU_CMD = BotCommand(command="menu", description="главное меню")

# name сервиса → ярлык раздела (кнопка главного меню). Только эти сервисы —
# «разделы»; остальное (если появится) в меню не попадёт автоматически.
SECTION_LABELS = {
    "vacancy": "📋 Вакансии",
    "dewarp": "🖼 Dewarp",
    "ask": "🤖 Ask",
}


def _main_kb(services: list[ServiceModule]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=SECTION_LABELS[s.name])]
        for s in services if s.name in SECTION_LABELS
    ]
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, input_field_placeholder="Выбери раздел"
    )


def _section_kb(service: ServiceModule) -> ReplyKeyboardMarkup:
    # кнопки = слэш-команды сервиса: тап отправляет «/cmd» → срабатывает его хендлер
    buttons = [KeyboardButton(text=f"/{c.command}") for c in service.commands]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]   # по 2 в ряд
    rows.append([KeyboardButton(text=MENU_LABEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_navigation_router(services: list[ServiceModule]) -> Router:
    """Навигация: главное меню + разделы. Раздел переключает reply-клавиатуру и
    /-меню чата на свои команды (декларативно из реестра). Логику сервисов не дублирует."""
    router = Router(name="navigation")
    by_label = {SECTION_LABELS[s.name]: s for s in services if s.name in SECTION_LABELS}
    main_kb = _main_kb(services)

    async def show_main(message: Message, state: FSMContext) -> None:
        await state.clear()                                   # сброс любого начатого флоу
        await message.bot.set_my_commands(
            [MENU_CMD], scope=BotCommandScopeChat(chat_id=message.chat.id)
        )
        await message.answer("Главное меню. Выбери раздел:", reply_markup=main_kb)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await message.answer(
            "Привет! Я vacancy-bot: вакансии, выпрямление документов и Q&A по разработке."
        )
        await show_main(message, state)

    @router.message(Command("menu"))
    async def menu(message: Message, state: FSMContext) -> None:
        await show_main(message, state)

    @router.message(F.text == MENU_LABEL)
    async def back_to_menu(message: Message, state: FSMContext) -> None:
        await show_main(message, state)

    @router.message(F.text.in_(set(by_label)))
    async def enter_section(message: Message, state: FSMContext) -> None:
        await state.clear()
        service = by_label[message.text]
        # /-меню чата → команды раздела + возврат в меню
        await message.bot.set_my_commands(
            [*service.commands, MENU_CMD],
            scope=BotCommandScopeChat(chat_id=message.chat.id),
        )
        await message.answer(
            f"Раздел «{SECTION_LABELS[service.name]}».\n\n{service.help_text}",
            reply_markup=_section_kb(service),
        )

    return router
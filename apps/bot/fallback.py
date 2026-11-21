"""Последний роутер: всё, что не поймали команды, вход и кнопки, — короткая подсказка."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from apps.bot.account import main_menu

router = Router()


# Без StateFilter: состояние входа ловит account раньше, а сюда доходят и
# неизвестные команды, прервавшие вход (FSM к этому моменту уже сброшен).
@router.message()
async def handle_any(message: Message) -> None:
    await message.answer(
        "Не понял. /help — список команд.\n\n"
        "Или открой приложение 👇",
        reply_markup=main_menu(),
    )


@router.callback_query()
async def handle_stale_button(query: CallbackQuery) -> None:
    await query.answer("Кнопка устарела — открой /start", show_alert=True)

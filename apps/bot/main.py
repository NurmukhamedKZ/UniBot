"""Тонкий aiogram-бот: /start и любое сообщение -> кнопка Mini App."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo

from packages.shared.config import TELEGRAM_TOKEN, WEBAPP_URL

router = Router()


@router.message()
async def handle_any(message: Message) -> None:
    await message.answer(
        "UniBot — бесплатно для студентов KBTU: отмечает видеолекции uninet просмотренными "
        "и делает транскрибы лекций.\n\nОткрой приложение 👇",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть UniBot", web_app=WebAppInfo(url=WEBAPP_URL))]]
        ),
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TELEGRAM_TOKEN)
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="UniBot", web_app=WebAppInfo(url=WEBAPP_URL)))
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

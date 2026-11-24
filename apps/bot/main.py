"""aiogram-бот UniBot: команды без Mini App + кнопки отметки на wsp.

Каталог uninet бот читает сам (``packages/core/uninet.py``, ``packages/db``):
Mini App для команд не нужен, только Telegram. Кнопка Mini App остаётся в
/start и /help как альтернатива.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, MenuButtonCommands

from packages.db.pool import create_pool
from packages.shared.config import TELEGRAM_TOKEN

from apps.bot import account, attendance, catalog, fallback

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Меню"),
    BotCommand(command="courses", description="Мои курсы"),
    BotCommand(command="course", description="Недели курса"),
    BotCommand(command="week", description="Видео недели"),
    BotCommand(command="watch", description="Смотреть неделю / весь курс"),
    BotCommand(command="transcribe", description="Транскриб видео"),
    BotCommand(command="wsp", description="Отметка посещения"),
    BotCommand(command="status", description="Активные задачи"),
    BotCommand(command="cancel", description="Отменить очередь"),
    BotCommand(command="login", description="Войти в KBTU"),
    BotCommand(command="logout", description="Удалить креды"),
    BotCommand(command="me", description="Аккаунт"),
    BotCommand(command="help", description="Все команды"),
]


def build_dispatcher(pool) -> Dispatcher:
    dp = Dispatcher(pool=pool, storage=MemoryStorage())
    # Только личка: в группе /login с паролем увидят все, а удалить его бот может не суметь.
    dp.message.filter(F.chat.type == ChatType.PRIVATE)
    # account — первым: его перехватчик сбрасывает вход, если посреди него пришла команда.
    dp.include_routers(account.router, catalog.router, attendance.router, fallback.router)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    pool = await create_pool()
    await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
    # Кнопка у поля ввода — список команд; Mini App открывается кнопкой в /start (account.main_menu).
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    dp = build_dispatcher(pool)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

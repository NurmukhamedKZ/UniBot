"""Тонкий aiogram-бот: /start и любое сообщение -> кнопка Mini App; кнопки отметки на wsp."""

from __future__ import annotations

import asyncio
import logging

import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from packages.core import wsp
from packages.core.uninet import InvalidCredentials, UninetUnavailable
from packages.db import users
from packages.db.pool import create_pool
from packages.shared.config import TELEGRAM_TOKEN, WEBAPP_URL
from packages.shared.crypto import decrypt_password

logger = logging.getLogger(__name__)
router = Router()

MARK_ALERTS = {
    wsp.MarkResult.UNCONFIRMED: "Нажал «Отметиться», но wsp не подтвердил отметку. Проверь на сайте.",
    wsp.MarkResult.CLOSED: "Этой пары уже нет среди открытых: окно закрылось или ты уже отметился.",
}


@router.message()
async def handle_any(message: Message) -> None:
    await message.answer(
        "UniBot — бесплатно для студентов KBTU: отмечает видеолекции uninet просмотренными "
        "и делает транскрибы лекций.\n\nОткрой приложение 👇",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть UniBot", web_app=WebAppInfo(url=WEBAPP_URL))]]
        ),
    )


@router.callback_query(F.data == wsp.MARK_DONE)
async def handle_mark_done(query: CallbackQuery) -> None:
    await query.answer("Уже отмечено ✅")


@router.callback_query(F.data.startswith(wsp.MARK_PREFIX))
async def handle_mark(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    """Кнопка «Отметиться» из уведомления воркера: отмечаем на wsp от имени нажавшего."""
    user = await users.credentials_by_telegram_id(pool, query.from_user.id)
    if user is None:
        await query.answer("Сначала войди в UniBot с логином KBTU.", show_alert=True)
        return
    try:
        result = await wsp.mark(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]),
                                query.data.removeprefix(wsp.MARK_PREFIX))
    except InvalidCredentials:
        await query.answer("wsp не принял пароль KBTU. Обнови его в UniBot.", show_alert=True)
        return
    except UninetUnavailable as exc:
        logger.warning("wsp mark failed for user %s: %r", user["id"], exc)
        await query.answer("wsp не отвечает. Попробуй ещё раз или отметься на сайте.", show_alert=True)
        return
    logger.info("wsp mark for user %s: %s", user["id"], result.value)
    if result is not wsp.MarkResult.MARKED:
        await query.answer(MARK_ALERTS[result], show_alert=True)
        return
    await query.answer("Отмечено ✅")
    await query.message.edit_reply_markup(reply_markup=_mark_as_done(query.message.reply_markup, query.data))


def _mark_as_done(markup: InlineKeyboardMarkup, data: str) -> InlineKeyboardMarkup:
    """Заменить нажатую кнопку на «✅ Отмечено: …», остальные оставить как есть."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b.text.replace("✅ Отметиться:", "✅ Отмечено:"), callback_data=wsp.MARK_DONE)
         if b.callback_data == data else b for b in row]
        for row in markup.inline_keyboard
    ])


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TELEGRAM_TOKEN)
    pool = await create_pool()
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="UniBot", web_app=WebAppInfo(url=WEBAPP_URL)))
    dp = Dispatcher(pool=pool)
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

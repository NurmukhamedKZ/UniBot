"""Посещаемость wsp: /wsp и кнопки «Отметиться» (в т.ч. из уведомлений воркера)."""

from __future__ import annotations

import logging

import asyncpg
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from packages.core import wsp
from packages.core.uninet import InvalidCredentials, UninetUnavailable
from packages.db import users
from packages.shared.crypto import decrypt_password

from apps.bot.common import LOGIN_HINT, credentials, edit, esc, typing

logger = logging.getLogger(__name__)
router = Router()

MARK_ALERTS = {
    wsp.MarkResult.UNCONFIRMED: "Нажал «Отметиться», но wsp не подтвердил отметку. Проверь на сайте.",
    wsp.MarkResult.CLOSED: "Этой пары уже нет среди открытых: окно закрылось или ты уже отметился.",
}


async def _attendance_view(pool: asyncpg.Pool, telegram_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    creds = await credentials(pool, telegram_id)
    if creds is None:
        return LOGIN_HINT, None
    try:
        page = await wsp.open_page(creds["kbtu_email"], decrypt_password(creds["kbtu_password_enc"]))
    except InvalidCredentials:
        return "KBTU не принял пароль. Обнови вход: /login", None
    except UninetUnavailable:
        return "wsp сейчас не отвечает. Попробуй позже.", None
    page_texts = wsp.texts(page)
    if not wsp.is_open(page_texts):
        return "Сейчас отметка посещения закрыта.", InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить ещё раз", callback_data="at:open")],
        ])
    return (
        "🔔 <b>Открыта отметка посещения</b>\n\n" + esc(wsp.attendance_text(page_texts)),
        wsp.attendance_keyboard(page),
    )


@router.message(Command("wsp", "attendance"))
async def cmd_wsp(message: Message, pool: asyncpg.Pool) -> None:
    await typing(message)
    text, markup = await _attendance_view(pool, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "at:open")
async def cb_attendance(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    await typing(query)
    text, markup = await _attendance_view(pool, query.from_user.id)
    await edit(query, text, markup)


@router.callback_query(F.data == wsp.MARK_DONE)
async def handle_mark_done(query: CallbackQuery) -> None:
    await query.answer("Уже отмечено ✅")


@router.callback_query(F.data.startswith(wsp.MARK_PREFIX))
async def handle_mark(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    """Кнопка «Отметиться» из уведомления воркера: отмечаем на wsp от имени нажавшего."""
    user = await users.credentials_by_telegram_id(pool, query.from_user.id)
    if user is None:
        await query.answer(LOGIN_HINT, show_alert=True)
        return
    try:
        result = await wsp.mark(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]),
                                query.data.removeprefix(wsp.MARK_PREFIX))
    except InvalidCredentials:
        await query.answer("wsp не принял пароль KBTU. Обнови вход: /login", show_alert=True)
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
    if getattr(query.message, "reply_markup", None) is not None:  # старое сообщение недоступно
        try:
            await query.message.edit_reply_markup(
                reply_markup=_mark_as_done(query.message.reply_markup, query.data)
            )
        except TelegramBadRequest:
            logger.warning("could not update mark keyboard", exc_info=True)


def _mark_as_done(markup: InlineKeyboardMarkup, data: str) -> InlineKeyboardMarkup:
    """Заменить нажатую кнопку на «✅ Отмечено: …», остальные оставить как есть."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b.text.replace("✅ Отметиться:", "✅ Отмечено:"), callback_data=wsp.MARK_DONE)
         if b.callback_data == data else b for b in row]
        for row in markup.inline_keyboard
    ])

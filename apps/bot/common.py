"""Общее для команд бота: контекст списков, форматирование, ошибки uninet.

Контекст (последний показанный курс/неделя) живёт в памяти процесса: бот —
один процесс, а после рестарта юзер просто заново откроет /courses. Кнопки
реальные id носят в ``callback_data``, так что им контекст не нужен.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field

import asyncpg
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from packages.core.uninet import InvalidCredentials
from packages.db import users

logger = logging.getLogger(__name__)


def esc(value) -> str:
    """Экранировать динамический текст для parse_mode=HTML."""
    return html.escape(str(value if value is not None else ""))


def fmt_duration(sec) -> str:
    """Секунды → ``m:ss`` / ``h:mm:ss`` (``—`` для неизвестного)."""
    if sec is None:
        return "—"
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Context:
    """Последние списки, показанные юзеру: по ним работают /course, /week, /transcribe."""

    courses: list[dict] = field(default_factory=list)
    course_id: int | None = None
    course: dict | None = None
    weeks: list[dict] = field(default_factory=list)
    week_id: int | None = None
    week: dict | None = None
    videos: list[dict] = field(default_factory=list)


_contexts: dict[int, Context] = {}


def context(telegram_id: int) -> Context:
    return _contexts.setdefault(telegram_id, Context())


async def user_row(pool: asyncpg.Pool, tg_user) -> asyncpg.Record:
    """Строка users для Telegram-юзера (создаёт при первом обращении)."""
    row, _ = await users.get_or_create_user(
        pool, tg_user.id, tg_user.username, tg_user.first_name
    )
    return row


async def credentials(pool: asyncpg.Pool, telegram_id: int) -> asyncpg.Record | None:
    return await users.credentials_by_telegram_id(pool, telegram_id)


LOGIN_HINT = "Сначала войди в KBTU: /login"


def uninet_error(exc: Exception) -> str:
    """Человеческий текст для типовых ошибок uninet/wsp."""
    if isinstance(exc, InvalidCredentials):
        return "KBTU не принял логин или пароль. Обнови вход: /login"
    return "uninet сейчас не отвечает. Попробуй ещё раз через минуту."


async def edit(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Отредактировать сообщение под кнопкой; «not modified» — не ошибка.

    Старое (>48 ч) сообщение приходит как ``InaccessibleMessage`` без ``edit_text``,
    а редактирование может не пройти и по другим причинам — тогда шлём новым.
    """
    try:
        if not isinstance(query.message, Message):
            raise LookupError("message is inaccessible")
        await query.message.edit_text(text, reply_markup=markup)
    except (TelegramBadRequest, LookupError) as exc:
        if "message is not modified" not in str(exc):
            logger.warning("edit failed, sending new message: %r", exc)
            await query.bot.send_message(query.from_user.id, text, reply_markup=markup)
    await query.answer()


async def typing(target: Message | CallbackQuery) -> None:
    """Показать «печатает…», пока идёт запрос в uninet; ошибки не важны."""
    message = target.message if isinstance(target, CallbackQuery) else target
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except Exception:
        logger.debug("typing action failed", exc_info=True)

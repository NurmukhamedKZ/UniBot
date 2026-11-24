"""Команды аккаунта и служебные: /start, /help, /login, /logout, /me, /status, /cancel."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from packages.core import uninet
from packages.core.uninet import InvalidCredentials, UninetUnavailable
from packages.db import catalog, tasks, users
from packages.shared.config import WEBAPP_URL
from packages.shared.crypto import encrypt_password

from apps.bot.common import edit, esc, uninet_error, user_row

logger = logging.getLogger(__name__)
router = Router()

WELCOME = (
    "<b>UniBot</b> — бесплатно для студентов KBTU: отмечает видеолекции uninet "
    "просмотренными и делает транскрибы.\n\n"
    "Всё можно делать командами, без приложения. /help — список."
)

HELP = (
    "<b>UniBot — команды</b>\n\n"
    "📚 <b>Каталог</b>\n"
    "/courses — мои курсы\n"
    "/course N — недели курса\n"
    "/week N — видео недели\n"
    "/watch N — смотреть неделю\n"
    "/watch all — весь курс\n"
    "/transcribe N — транскриб видео\n"
    "/transcribe all — транскрибы всей недели\n\n"
    "✅ <b>Посещаемость</b>\n"
    "/wsp — открытые пары и кнопка «Отметиться» (алиас /attendance)\n\n"
    "⚙️ <b>Аккаунт и задачи</b>\n"
    "/login — войти в KBTU\n"
    "/logout — удалить креды\n"
    "/me — аккаунт\n"
    "/status — активные задачи\n"
    "/cancel — отменить очередь\n"
    "/menu — меню (то же, что /start)\n"
    "/help — эта справка\n\n"
    "<i>N — позиция в последнем списке (1, 2, 3…), а не номер недели.</i>"
)

PASSWORD_PROMPT = (
    "Теперь пароль KBTU одним сообщением — я сразу удалю его из чата.\n"
    "Если бот перезапустится посреди входа, пришли /login заново.\n"
    "Отменить: /cancel"
)


class Login(StatesGroup):
    email = State()
    password = State()


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📚 Мои курсы", callback_data="cl")],
        [InlineKeyboardButton(text="✅ Отметка wsp", callback_data="at:open")],
        [InlineKeyboardButton(text="⚙️ Аккаунт", callback_data="me")],
        [InlineKeyboardButton(text="🔑 Войти в KBTU", callback_data="login")],
    ]
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=WEBAPP_URL))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(StateFilter(Login), F.text.startswith("/"))
async def command_during_login(message: Message, state: FSMContext) -> None:
    """Команда посреди входа: вход прерываем, команду отдаём её хендлеру.

    Иначе ``/courses`` в состоянии пароля ушёл бы в Keycloak как пароль. После
    ``SkipHandler`` фильтры дальше видят ещё старое состояние, поэтому хендлеры
    входа сами отсекают команды (``~F.text.startswith("/")``).
    """
    await state.clear()
    if message.text.split()[0].split("@")[0] == "/cancel":
        await message.answer("Вход отменён.")
        return
    raise SkipHandler()


@router.message(Command("start", "menu"))
async def cmd_start(message: Message, pool: asyncpg.Pool) -> None:
    await user_row(pool, message.from_user)
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=main_menu())


@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) == 3:
        email, password = parts[1], parts[2]
        if "@" not in email:
            await message.answer("Это не похоже на email. Попробуй: /login email password")
            return
        await state.clear()
        await _delete_or_warn(message)
        await _save_login(message, pool, email, password)
        return
    if len(parts) == 2:
        email = parts[1]
        if "@" not in email:
            await message.answer("Это не похоже на email. Попробуй: /login или /login email password")
            return
        await state.clear()
        await state.update_data(email=email)
        await state.set_state(Login.password)
        await message.answer(PASSWORD_PROMPT)
        return
    await state.clear()
    await state.set_state(Login.email)
    await message.answer("Введи email KBTU (например, student@kbtu.kz).\nОтменить: /cancel")


@router.message(Command("logout"))
async def cmd_logout(message: Message, pool: asyncpg.Pool) -> None:
    row = await user_row(pool, message.from_user)
    if row["kbtu_email"] is None:
        await message.answer("Ты и так не вошёл в KBTU.")
        return
    cancelled = await tasks.cancel_queued(pool, row["id"])
    running = await tasks.running_for_user(pool, row["id"])
    await users.set_credentials(pool, row["id"], None, None)
    lines = ["Вышел. Сохранённые креды KBTU удалены."]
    if cancelled:
        lines.append(f"Отменил задач в очереди: {cancelled}.")
    if running:
        lines.append(f"Уже запущенные ({running}) досмотрятся до конца на старой сессии.")
    await message.answer("\n".join(lines))


@router.message(Command("me"))
async def cmd_me(message: Message, pool: asyncpg.Pool) -> None:
    await message.answer(await _me_text(pool, message.from_user), reply_markup=_me_keyboard())


@router.message(Command("status"))
async def cmd_status(message: Message, pool: asyncpg.Pool) -> None:
    row = await user_row(pool, message.from_user)
    await message.answer(await _status_text(pool, row["id"]))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    if await state.get_state() is not None:
        await state.clear()
        await message.answer("Отменено.")
        return
    row = await user_row(pool, message.from_user)
    cancelled = await tasks.cancel_queued(pool, row["id"])
    running = await tasks.running_for_user(pool, row["id"])
    text = f"Отменил задач в очереди: {cancelled}." if cancelled else "В очереди нечего отменять."
    if running:
        text += f" Уже запущенные ({running}) досмотрятся до конца."
    await message.answer(text)


# --- Вход: пошагово email → пароль -------------------------------------------------


@router.message(StateFilter(Login.email), ~F.text.startswith("/"))
async def login_email(message: Message, state: FSMContext) -> None:
    email = (message.text or "").strip()
    if "@" not in email:
        await message.answer("Это не похоже на email. Пришли ещё раз или /cancel.")
        return
    await state.update_data(email=email)
    await state.set_state(Login.password)
    await message.answer(PASSWORD_PROMPT)


@router.message(StateFilter(Login.password), ~F.text.startswith("/"))
async def login_password(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    password = message.text or ""
    email = (await state.get_data()).get("email", "")
    await state.clear()
    await _delete_or_warn(message)
    await _save_login(message, pool, email, password)


async def _save_login(message: Message, pool: asyncpg.Pool, email: str, password: str) -> None:
    if not email or not password:
        await message.answer("Нужны и email, и пароль. Попробуй снова: /login")
        return
    try:
        # Не через uninet.session(): неудачный пароль не должен оседать в кэше сессий.
        await uninet.Uninet(email, password).login()
    except InvalidCredentials:
        await message.answer("KBTU не принял логин или пароль. Попробуй снова: /login")
        return
    except UninetUnavailable as exc:
        await message.answer(uninet_error(exc))
        return
    row = await user_row(pool, message.from_user)
    await users.set_credentials(pool, row["id"], email, encrypt_password(password))
    logger.info("credentials saved for user %s", row["id"])
    await message.answer(
        f"✅ Готово, вошёл как <b>{esc(email)}</b>.\n\n/courses — мои курсы, /wsp — отметка посещения"
    )


async def _delete_or_warn(message: Message) -> None:
    if not await _delete(message):
        await message.answer(
            "⚠️ Не смог удалить сообщение с паролем — удали его вручную и смени пароль KBTU."
        )


async def _delete(message: Message) -> bool:
    """Удалить сообщение (пароль не должен оставаться в истории чата)."""
    try:
        await message.delete()
        return True
    except TelegramAPIError:
        logger.warning("could not delete message %s", message.message_id, exc_info=True)
        return False


# --- Аккаунт и задачи --------------------------------------------------------------


def _me_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Мои курсы", callback_data="cl")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
    ])


async def _me_text(pool: asyncpg.Pool, tg_user) -> str:
    row = await user_row(pool, tg_user)
    active = await tasks.active_for_user(pool, row["id"])
    if row["kbtu_email"]:
        account = f"✅ {esc(row['kbtu_email'])}"
    else:
        account = "не задан — /login"
    return (
        "👤 <b>Аккаунт</b>\n\n"
        f"KBTU: {account}\n"
        f"Активных задач: {len(active)}"
    )


async def _status_text(pool: asyncpg.Pool, user_id) -> str:
    active = await tasks.active_for_user(pool, user_id)
    if not active:
        return "Активных задач нет."
    now = datetime.now(timezone.utc)
    lines = ["<b>⏳ Активные задачи</b>", ""]
    for task in active:
        if task["type"] == "watch":
            cached = await catalog.cached_week(pool, int(task["lesson_id"]))
            theme = cached[0].get("theme") if cached else None
            label = f"Неделя · {esc(theme)}" if theme else f"Неделя #{task['lesson_id']}"
            icon = "▶️" if task["status"] == "running" else "⏸"
        else:
            title = await catalog.video_title(pool, task["lesson_id"])
            label = f"Транскриб · {esc(title)}" if title else f"Транскриб видео {esc(str(task['lesson_id'])[:10])}…"
            icon = "📝"
        suffix = ""
        if task["status"] == "running" and task["started_at"]:
            minutes = int((now - task["started_at"]).total_seconds() // 60)
            suffix = f" — идёт {minutes} мин"
        lines.append(f"{icon} {label}{suffix}")
    return "\n".join(lines)


@router.callback_query(F.data == "home")
async def cb_home(query: CallbackQuery) -> None:
    await edit(query, WELCOME, main_menu())


@router.callback_query(F.data == "me")
async def cb_me(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    await edit(query, await _me_text(pool, query.from_user), _me_keyboard())


@router.callback_query(F.data == "login")
async def cb_login(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(Login.email)
    await edit(query, "Введи email KBTU (например, student@kbtu.kz).\nОтменить: /cancel", None)

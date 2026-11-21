"""Маршрутизация бота целиком (настоящий Dispatcher, Telegram подменён записью запросов)."""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from html.parser import HTMLParser

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import Chat, Message, Update, User

from apps.bot.account import HELP, WELCOME, Login
from apps.bot.common import LOGIN_HINT
from apps.bot.main import build_dispatcher

_ids = itertools.count(1)
TG_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler"}


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self) -> None:
        pass


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.bad: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in TG_TAGS:
            self.bad.append(tag)


def assert_telegram_html(text: str) -> None:
    """Telegram отклоняет сообщение целиком на любом неизвестном теге (``<номер>``)."""
    parser = _Tags()
    parser.feed(text)
    assert parser.bad == [], f"unsupported tags {parser.bad} in {text!r}"


_dp = None


async def _setup(pool):
    # Роутеры — модульные и подключаются к диспетчеру один раз; pool у тестов общий.
    global _dp
    if _dp is None:
        _dp = build_dispatcher(pool)
    session = RecordingSession()
    bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return bot, session, _dp


async def _send(dp, bot, text: str, user_id: int, chat_type: str = "private") -> None:
    message = Message(
        message_id=next(_ids),
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id if chat_type == "private" else -100, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="T"),
        text=text,
    )
    await dp.feed_update(bot, Update(update_id=next(_ids), message=message))


def _texts(session) -> list[str]:
    return [m.text for m in session.calls if isinstance(m, SendMessage)]


async def test_command_during_password_step_is_not_taken_as_password(pool) -> None:
    bot, session, dp = await _setup(pool)
    state = dp.fsm.get_context(bot, chat_id=1001, user_id=1001)
    await state.set_state(Login.password)

    await _send(dp, bot, "/courses", 1001)

    assert _texts(session) == [LOGIN_HINT]  # отработал /courses, а не вход
    assert not any(isinstance(m, DeleteMessage) for m in session.calls)
    assert await state.get_state() is None


async def test_cancel_during_login(pool) -> None:
    bot, session, dp = await _setup(pool)
    state = dp.fsm.get_context(bot, chat_id=1002, user_id=1002)
    await state.set_state(Login.email)

    await _send(dp, bot, "/cancel", 1002)

    assert _texts(session) == ["Вход отменён."]
    assert await state.get_state() is None


async def test_unknown_command_during_login_falls_back(pool) -> None:
    bot, session, dp = await _setup(pool)
    state = dp.fsm.get_context(bot, chat_id=1003, user_id=1003)
    await state.set_state(Login.password)

    await _send(dp, bot, "/nonsense", 1003)

    assert "Не понял" in _texts(session)[0]
    assert await state.get_state() is None


async def test_group_messages_are_ignored(pool) -> None:
    bot, session, dp = await _setup(pool)

    await _send(dp, bot, "/login a@kbtu.kz secret", 1004, chat_type="supergroup")

    assert session.calls == []


async def test_hints_without_arguments_are_valid_html(pool) -> None:
    bot, session, dp = await _setup(pool)

    for command in ("/course", "/week", "/watch", "/transcribe", "/help", "/start", "/status"):
        await _send(dp, bot, command, 1005)

    texts = _texts(session)
    assert len(texts) == 7
    for text in texts + [HELP, WELCOME]:
        assert_telegram_html(text)

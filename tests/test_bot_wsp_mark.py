"""Кнопка «Отметиться» в уведомлении: бот отмечает на wsp от имени нажавшего."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from apps.bot import main as bot
from packages.core import wsp
from packages.core.uninet import UninetUnavailable
from packages.db import users
from packages.shared.crypto import encrypt_password

L3, L4 = wsp.MARK_PREFIX + "aaa", wsp.MARK_PREFIX + "bbb"


class FakeMessage:
    def __init__(self):
        self.reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отметиться: БД Л3", callback_data=L3)],
            [InlineKeyboardButton(text="✅ Отметиться: БД Л4", callback_data=L4)],
            [InlineKeyboardButton(text="Открыть wsp.kbtu.kz", url=wsp.BASE)],
        ])
        self.edited = None

    async def edit_reply_markup(self, reply_markup):
        self.edited = reply_markup


class FakeUser:
    def __init__(self, id):
        self.id = id


class FakeQuery:
    def __init__(self, telegram_id, data):
        self.from_user, self.data, self.message = FakeUser(telegram_id), data, FakeMessage()
        self.answers = []

    async def answer(self, text, show_alert=False):
        self.answers.append((text, show_alert))


async def with_creds(pool, make_user, telegram_id=42):
    uid = await make_user(telegram_id=telegram_id)
    await users.set_credentials(pool, uid, "n_test@kbtu.kz", encrypt_password("pw"))


async def test_mark_success_replaces_button(pool, make_user, monkeypatch):
    await with_creds(pool, make_user)
    calls = []

    async def fake_mark(email, password, key):
        calls.append((email, password, key))
        return wsp.MarkResult.MARKED

    monkeypatch.setattr(wsp, "mark", fake_mark)
    query = FakeQuery(42, L3)
    await bot.handle_mark(query, pool)

    assert calls == [("n_test@kbtu.kz", "pw", "aaa")]
    assert query.answers == [("Отмечено ✅", False)]
    rows = query.message.edited.inline_keyboard
    assert (rows[0][0].text, rows[0][0].callback_data) == ("✅ Отмечено: БД Л3", wsp.MARK_DONE)
    assert rows[1][0].callback_data == L4 and rows[2][0].url == wsp.BASE


async def test_mark_not_confirmed_or_closed_keeps_button(pool, make_user, monkeypatch):
    await with_creds(pool, make_user)
    for result in (wsp.MarkResult.UNCONFIRMED, wsp.MarkResult.CLOSED):
        async def fake_mark(email, password, key, result=result):
            return result

        monkeypatch.setattr(wsp, "mark", fake_mark)
        query = FakeQuery(42, L3)
        await bot.handle_mark(query, pool)
        assert query.answers == [(bot.MARK_ALERTS[result], True)]
        assert query.message.edited is None


async def test_mark_wsp_down(pool, make_user, monkeypatch):
    await with_creds(pool, make_user)

    async def fake_mark(email, password, key):
        raise UninetUnavailable("wsp: timeout")

    monkeypatch.setattr(wsp, "mark", fake_mark)
    query = FakeQuery(42, L3)
    await bot.handle_mark(query, pool)
    assert query.answers[0][1] is True and "не отвечает" in query.answers[0][0]


async def test_mark_without_credentials(pool, make_user, monkeypatch):
    await make_user(telegram_id=7)

    async def fail(*a):
        raise AssertionError("wsp must not be called without credentials")

    monkeypatch.setattr(wsp, "mark", fail)
    query = FakeQuery(7, L3)
    await bot.handle_mark(query, pool)
    assert query.answers[0][1] is True and query.message.edited is None

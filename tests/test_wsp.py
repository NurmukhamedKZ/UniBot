"""Слежка за «Отметкой посещения» wsp: разбор страницы и уведомление только на открытии."""

from __future__ import annotations

from packages.core import wsp
from packages.db import users
from packages.shared.crypto import encrypt_password

CLOSED = ["Отметка посещения", "Нет доступных дисциплин"]
OPEN = ["Отметка посещения", "Математика", "Отметиться"]


def test_is_open():
    assert not wsp.is_open(CLOSED)
    assert not wsp.is_open(["Attendance mark", "No disciplines available"])
    assert wsp.is_open(OPEN)


def test_attendance_text():
    page = ["Отметка посещения", "БД<br>Иванов<br>Л3 (12:00 - 13:00)", "Отметиться",
            "БД<br>Иванов<br>Л4 (13:00 - 14:00)", "Отметиться"]
    assert wsp.attendance_text(page) == "БД\nИванов\nЛ3 (12:00 - 13:00)\n\nБД\nИванов\nЛ4 (13:00 - 14:00)"


def test_texts_and_login():
    uidl = {"state": {"1": {"text": "Нет доступных дисциплин"}, "2": {"caption": "Отметиться"}, "3": {"width": "1"}}}
    assert wsp.texts(uidl) == ["Нет доступных дисциплин", "Отметиться"]
    assert wsp.wsp_login("n_ashekei@kbtu.kz") == "n_ashekei"
    assert wsp.wsp_login("n_ashekei") == "n_ashekei"


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


async def test_notifies_once_per_opening(pool, make_user, monkeypatch):
    from apps.worker import main as worker

    uid = await make_user(telegram_id=42)
    await users.set_credentials(pool, uid, "n_test@kbtu.kz", encrypt_password("pw"))
    bot, pages = FakeBot(), iter([CLOSED, OPEN, OPEN, CLOSED, OPEN])

    async def fake_texts(email, password):
        assert (email, password) == ("n_test@kbtu.kz", "pw")
        return next(pages)

    monkeypatch.setattr(wsp, "registration_texts", fake_texts)
    for _ in range(5):
        [user] = await users.with_credentials(pool)
        await worker.check_attendance(pool, bot, user)

    assert len(bot.sent) == 2  # два открытия, повторное OPEN молчит
    assert bot.sent[0][0] == 42 and "Математика" in bot.sent[0][1] and "Отметка посещения" not in bot.sent[0][1] \
        and "Отметиться" not in bot.sent[0][1]
    assert (await users.with_credentials(pool))[0]["wsp_open"] is True


async def test_new_credentials_reset_wsp_open(pool, make_user):
    uid = await make_user()
    await users.set_credentials(pool, uid, "a@kbtu.kz", encrypt_password("pw"))
    await users.set_wsp_open(pool, uid, True)
    await users.set_credentials(pool, uid, "b@kbtu.kz", encrypt_password("pw"))
    assert (await users.with_credentials(pool))[0]["wsp_open"] is False

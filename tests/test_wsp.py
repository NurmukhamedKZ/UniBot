"""Слежка за «Отметкой посещения» wsp: разбор страницы и уведомление только на открытии."""

from __future__ import annotations

from packages.core import wsp
from packages.db import users
from packages.shared.crypto import encrypt_password

CLOSED = ["Отметка посещения", "Нет доступных дисциплин"]
OPEN = ["Отметка посещения", "Математика", "Отметиться"]
L3 = "Деректер базасы<br>Құралбаев А.Т.<br>Л3 (12:00 - 13:00)<br>Осталось 49 мин."
L4 = "Деректер базасы<br>Құралбаев А.Т.<br>Л4 (13:00 - 14:00)<br>Осталось 99 мин."


def page(*labels: str) -> dict:
    """UIDL страницы как её отдаёт wsp: заголовок + на каждую пару Label и Button в своём контейнере."""
    types = {"1": 0, "2": 1}
    state = {"2": {"text": "Отметка посещения"}}
    hierarchy = {"1": ["2"]}
    for i, label in enumerate(labels or ["Нет доступных дисциплин"]):
        box, text, button = f"b{i}", f"t{i}", f"k{i}"
        types |= {box: 2, text: 1}
        state[text] = {"text": label}
        hierarchy["1"].append(box)
        hierarchy[box] = [text]
        if labels:
            types[button] = 3
            state[button] = {"caption": "Отметиться"}
            hierarchy[box].append(button)
    mappings = {"com.vaadin.ui.UI": 0, "com.vaadin.ui.Label": 1,
                "com.vaadin.ui.HorizontalLayout": 2, "com.vaadin.ui.Button": 3}
    return {"typeMappings": mappings, "types": types, "state": state, "hierarchy": hierarchy}


def test_is_open():
    assert not wsp.is_open(CLOSED)
    assert not wsp.is_open(["Attendance mark", "No disciplines available"])
    assert wsp.is_open(OPEN)


def test_attendance_text():
    page = ["Отметка посещения", "БД<br>Иванов<br>Л3 (12:00 - 13:00)", "Отметиться",
            "БД<br>Иванов<br>Л4 (13:00 - 14:00)", "Отметиться"]
    assert wsp.attendance_text(page) == "БД\nИванов\nЛ3 (12:00 - 13:00)\n\nБД\nИванов\nЛ4 (13:00 - 14:00)"


def test_lessons_from_uidl():
    lessons = wsp.lessons(page(L3, L4))
    assert [(x.label, x.button) for x in lessons] == [(L3, "k0"), (L4, "k1")]
    assert [x.title for x in lessons] == ["Деректер базасы Л3", "Деректер базасы Л4"]
    assert wsp.lessons(page()) == []


def test_lesson_key_survives_countdown():
    later = wsp.Lesson(L3.replace("49 мин.", "12 мин."), "other-id")
    assert later.key == wsp.lessons(page(L3))[0].key
    assert later.key != wsp.lessons(page(L4))[0].key
    assert len(wsp.MARK_PREFIX + later.key) <= 64  # лимит callback_data в Telegram


def test_texts_and_login():
    uidl = {"state": {"1": {"text": "Нет доступных дисциплин"}, "2": {"caption": "Отметиться"}, "3": {"width": "1"}}}
    assert wsp.texts(uidl) == ["Нет доступных дисциплин", "Отметиться"]
    assert wsp.wsp_login("a_student@kbtu.kz") == "a_student"
    assert wsp.wsp_login("a_student") == "a_student"


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


async def test_notifies_once_per_opening(pool, make_user, monkeypatch):
    from apps.worker import main as worker

    uid = await make_user(telegram_id=42)
    await users.set_credentials(pool, uid, "n_test@kbtu.kz", encrypt_password("pw"))
    bot, pages = FakeBot(), iter([page(), page(L3), page(L3), page(), page(L4)])

    async def fake_open_page(email, password):
        assert (email, password) == ("n_test@kbtu.kz", "pw")
        return next(pages)

    monkeypatch.setattr(wsp, "open_page", fake_open_page)
    for _ in range(5):
        [user] = await users.with_credentials(pool)
        await worker.check_attendance(pool, bot, user)

    assert len(bot.sent) == 2  # два открытия, повторное OPEN молчит
    chat_id, text, markup = bot.sent[0]
    assert chat_id == 42 and "Деректер базасы\nҚұралбаев А.Т.\nЛ3" in text
    assert "Отметка посещения" not in text and "Отметиться" not in text and "<br>" not in text
    [mark], [site] = markup.inline_keyboard
    assert mark.text == "✅ Отметиться: Деректер базасы Л3"
    assert mark.callback_data == wsp.MARK_PREFIX + wsp.lessons(page(L3))[0].key
    assert site.url == wsp.BASE
    assert (await users.with_credentials(pool))[0]["wsp_open"] is True


async def test_new_credentials_reset_wsp_open(pool, make_user):
    uid = await make_user()
    await users.set_credentials(pool, uid, "a@kbtu.kz", encrypt_password("pw"))
    await users.set_wsp_open(pool, uid, True)
    await users.set_credentials(pool, uid, "b@kbtu.kz", encrypt_password("pw"))
    assert (await users.with_credentials(pool))[0]["wsp_open"] is False


class FakeSession:
    def __init__(self, before: dict, after: dict):
        self.page, self._after, self.clicked = before, after, []

    async def click(self, button):
        self.clicked.append(button)

    async def reload(self):
        self.page = self._after


def fake_session(monkeypatch, before, after) -> FakeSession:
    from contextlib import asynccontextmanager

    session = FakeSession(before, after)

    @asynccontextmanager
    async def _session(email, password):
        yield session

    monkeypatch.setattr(wsp, "_session", _session)
    return session


async def test_mark_clicks_the_right_button(monkeypatch):
    key = wsp.lessons(page(L4))[0].key
    s = fake_session(monkeypatch, page(L3, L4), page(L3))
    assert await wsp.mark("n@kbtu.kz", "pw", key) is wsp.MarkResult.MARKED
    assert s.clicked == ["k1"]

    s = fake_session(monkeypatch, page(L3, L4), page(L3, L4))
    assert await wsp.mark("n@kbtu.kz", "pw", key) is wsp.MarkResult.UNCONFIRMED

    s = fake_session(monkeypatch, page(L3), page(L3))
    assert await wsp.mark("n@kbtu.kz", "pw", key) is wsp.MarkResult.CLOSED
    assert s.clicked == []

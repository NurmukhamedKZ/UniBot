"""Бот-команды: форматирование экранов и новые запросы очереди (без Telegram)."""

from __future__ import annotations

from apps.bot.catalog import _index, courses_text, send_transcript, video_text, week_text, weeks_keyboard
from apps.bot.common import fmt_duration
from packages.db import catalog as db_catalog
from packages.db import tasks, users
from packages.shared.crypto import encrypt_password


def test_fmt_duration() -> None:
    assert fmt_duration(None) == "—"
    assert fmt_duration(0) == "0:00"
    assert fmt_duration(59) == "0:59"
    assert fmt_duration(90) == "1:30"
    assert fmt_duration(3600) == "1:00:00"
    assert fmt_duration(3725) == "1:02:05"


def test_index_parses_only_positive_numbers() -> None:
    assert _index("1") == 0
    assert _index(" 3 ") == 2
    assert _index("0") is None
    assert _index("-1") is None
    assert _index("all") is None
    assert _index(None) is None


def test_courses_text_lists_courses_with_numbers() -> None:
    text = courses_text([
        {"id": 1, "code": "CS101", "name": "Intro", "teacher": "T"},
        {"id": 2, "code": "MATH", "name": "Algebra", "teacher": None},
    ])
    assert "1. <b>Intro</b> · CS101" in text
    assert "2. <b>Algebra</b> · MATH" in text
    assert "/course 1" in text


def test_courses_text_escapes_html() -> None:
    assert "&lt;b&gt;" in courses_text([{"id": 1, "name": "<b>"}])


def test_week_text_marks_completed_and_missing() -> None:
    text = week_text(
        {"name": "Intro"},
        {"weekNumber": 3, "theme": "Trees", "dueDate": "2026-11-20T00:00:00"},
        [
            {"title": "L1", "durationSec": 60, "completed": True},
            {"title": "L2", "durationSec": 3661, "completed": False},
        ],
    )
    assert "Неделя 3" in text
    assert "✅ L1 · 1:00" in text
    assert "▶️ L2 · 1:01:01" in text
    assert "Дедлайн: 2026-11-20" in text


def test_video_text_shows_progress() -> None:
    text = video_text({
        "title": "L2",
        "durationSec": 100,
        "cumulativeWatchSec": 30,
        "requiredSec": 90,
        "completed": False,
    })
    assert "Просмотрено: 0:30 / 1:30" in text
    assert "не просмотрено" in text


def test_weeks_keyboard_skips_locked_weeks() -> None:
    markup = weeks_keyboard(7, [
        {"id": 1, "weekNumber": 1, "theme": "A", "unlocked": True},
        {"id": 2, "weekNumber": 2, "theme": "B", "unlocked": False},
    ])
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "w:7:1" in data
    assert "w:7:2" not in data
    assert "wc:7" in data


async def test_active_for_user_lists_queued_and_running(pool, make_user) -> None:
    user_id = await make_user()
    queued = await tasks.enqueue(pool, user_id, "watch", "1")
    running = await tasks.enqueue(pool, user_id, "watch", "2")
    await pool.execute("UPDATE watch_tasks SET status='running' WHERE id=$1", running)

    active = await tasks.active_for_user(pool, user_id)

    assert [row["id"] for row in active] == [queued, running]


async def test_cancel_queued_leaves_running_and_other_users(pool, make_user) -> None:
    user_id = await make_user()
    other_id = await make_user()
    queued = await tasks.enqueue(pool, user_id, "watch", "1")
    running = await tasks.enqueue(pool, user_id, "watch", "2")
    await pool.execute("UPDATE watch_tasks SET status='running' WHERE id=$1", running)
    other = await tasks.enqueue(pool, other_id, "watch", "3")

    assert await tasks.cancel_queued(pool, user_id) == 1
    assert await pool.fetchval("SELECT status FROM watch_tasks WHERE id=$1", queued) == "cancelled"
    assert await pool.fetchval("SELECT status FROM watch_tasks WHERE id=$1", running) == "running"
    assert await pool.fetchval("SELECT status FROM watch_tasks WHERE id=$1", other) == "queued"


class FakeMessage:
    """Минимум, который нужен send_transcript: файл или текст."""

    def __init__(self) -> None:
        self.documents: list[tuple[object, str | None]] = []
        self.answers: list[str] = []

    async def answer_document(self, document, caption=None) -> None:
        self.documents.append((document, caption))

    async def answer(self, text, **kwargs) -> None:
        self.answers.append(text)


async def _user_row(pool, make_user, with_creds: bool = False):
    user_id = await make_user()
    if with_creds:
        await users.set_credentials(pool, user_id, "n_test@kbtu.kz", encrypt_password("pw"))
    return await pool.fetchrow("SELECT * FROM users WHERE id=$1", user_id)


async def test_send_transcript_cached_sends_file(pool, make_user) -> None:
    await db_catalog.save_transcript(pool, "kid123456789", "hello")
    message = FakeMessage()

    await send_transcript(message, pool, await _user_row(pool, make_user), "kid123456789")

    assert message.answers == []
    document, caption = message.documents[0]
    assert document.filename == "transcript-kid123456789.txt"
    assert caption == "📝 Транскриб"


async def test_send_transcript_without_credentials_asks_login(pool, make_user) -> None:
    message = FakeMessage()

    await send_transcript(message, pool, await _user_row(pool, make_user), "missing")

    assert message.documents == []
    assert "/login" in message.answers[0]


async def test_send_transcript_enqueues_and_dedupes(pool, make_user) -> None:
    row = await _user_row(pool, make_user, with_creds=True)
    message = FakeMessage()

    await send_transcript(message, pool, row, "kid_new")
    assert "Готовлю" in message.answers[0]
    assert await tasks.latest_transcribe_task(pool, "kid_new") is not None

    again = FakeMessage()
    await send_transcript(again, pool, row, "kid_new")
    assert "уже готовится" in again.answers[0]
    count = await pool.fetchval(
        "SELECT count(*) FROM watch_tasks WHERE type='transcribe' AND lesson_id='kid_new'"
    )
    assert count == 1


async def test_cancel_queued_keeps_shared_transcribes_and_blocks_claim(pool, make_user) -> None:
    user_id = await make_user()
    transcribe = await tasks.enqueue(pool, user_id, "transcribe", "kid_shared")
    await tasks.enqueue(pool, user_id, "watch", "1")

    assert await tasks.cancel_queued(pool, user_id) == 1
    assert await tasks.claim_next(pool, "watch") is None
    assert await pool.fetchval("SELECT status FROM watch_tasks WHERE id=$1", transcribe) == "queued"

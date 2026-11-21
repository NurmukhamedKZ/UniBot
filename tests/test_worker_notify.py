"""Уведомления воркера о готовых задачах: статус пишется всегда, сообщений — ровно сколько нужно."""

from __future__ import annotations

import pytest

from apps.worker import main as worker
from packages.core import uninet
from packages.core.uninet import UninetUnavailable
from packages.db import catalog, tasks, users
from packages.shared.crypto import encrypt_password


class FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))

    async def send_document(self, chat_id, document, caption=None, **kwargs):
        self.sent.append((chat_id, caption))


class FakeSession:
    def __init__(self, result) -> None:
        self.result = result

    async def watch_week(self, week_id):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def _watch_task(pool, make_user, creds: bool = True):
    user_id = await make_user(telegram_id=77)
    if creds:
        await users.set_credentials(pool, user_id, "n_test@kbtu.kz", encrypt_password("pw"))
    task_id = await tasks.enqueue(pool, user_id, "watch", "5")
    return await tasks.get_task(pool, task_id)


async def _status(pool, task) -> str:
    return (await tasks.get_task(pool, task["id"]))["status"]


@pytest.mark.parametrize(("result", "status", "expected"), [
    (True, "done", ["✅ Неделя #5 просмотрена."]),
    (False, "done", []),  # уже была досмотрена — без лишнего сообщения
    (UninetUnavailable("down"), "failed", ["⚠️ Не удалось досмотреть неделю #5. Попробуй ещё раз: /watch"]),
])
async def test_run_watch_notifies(pool, make_user, monkeypatch, result, status, expected) -> None:
    monkeypatch.setattr(uninet, "session", lambda email, password: FakeSession(result))
    task = await _watch_task(pool, make_user)
    bot = FakeBot()

    await worker.run_watch(pool, bot, task)

    assert await _status(pool, task) == status
    assert bot.sent == [(77, text) for text in expected]


async def test_run_watch_without_credentials_suggests_login(pool, make_user) -> None:
    task = await _watch_task(pool, make_user, creds=False)
    bot = FakeBot()

    await worker.run_watch(pool, bot, task)

    assert await _status(pool, task) == "failed"
    assert "/login" in bot.sent[0][1]


async def test_notification_failure_keeps_task_done(pool, make_user, monkeypatch) -> None:
    monkeypatch.setattr(uninet, "session", lambda email, password: FakeSession(True))
    task = await _watch_task(pool, make_user)

    await worker.run_watch(pool, FakeBot(fail=True), task)

    assert await _status(pool, task) == "done"


async def test_run_transcribe_sends_file_with_title(pool, make_user) -> None:
    user_id = await make_user(telegram_id=78)
    await catalog.save_transcript(pool, "kid1", "hello")
    await catalog.cache_week(pool, 9, {"theme": "T"}, [{"id": 1, "title": "Лекция 1", "kinescope_id": "kid1"}])
    task = await tasks.get_task(pool, await tasks.enqueue(pool, user_id, "transcribe", "kid1"))
    bot = FakeBot()

    await worker.run_transcribe(pool, bot, task)

    assert await _status(pool, task) == "done"
    assert bot.sent == [(78, "📝 Транскриб готов: «Лекция 1»")]


async def test_notify_skips_task_without_user(pool) -> None:
    bot = FakeBot()

    async def text():
        raise AssertionError("caption must not be built without a user")

    await worker.notify(pool, bot, None, text)

    assert bot.sent == []

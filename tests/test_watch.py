"""Tests for apps/api/routers/watch.py: POST /watch, GET /tasks/active, cancel.

Follows the exact test-app-with-valid-initData pattern from tests/test_catalog.py:
mount a fresh FastAPI app with the real router, wire ``app.state.pool``
directly to the ``pool`` fixture (real local Postgres), and build valid
Telegram initData by hand with the same HMAC scheme ``apps.api.auth`` expects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.api.routers.watch import router as watch_router
from packages.db import ledger, tasks

pytestmark = pytest.mark.asyncio

TEST_BOT_TOKEN = "123456:test-bot-token"


def _build_init_data(bot_token: str, user: dict) -> str:
    data = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "AAtestquery",
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def _make_app(pool):
    app = FastAPI()
    app.include_router(watch_router)
    app.state.pool = pool
    return app


@pytest.fixture(autouse=True)
def _set_bot_token(monkeypatch):
    monkeypatch.setattr("apps.api.auth.TELEGRAM_TOKEN", TEST_BOT_TOKEN)


def _auth_headers(telegram_id: int) -> dict:
    init_data = _build_init_data(TEST_BOT_TOKEN, {"id": telegram_id, "username": "stu"})
    return {"X-Telegram-Init-Data": init_data}


async def _seed_lesson(pool, lesson_id="l1", course_id=None):
    if course_id:
        await pool.execute(
            "INSERT INTO courses (course_id, title) VALUES ($1, 'Course') "
            "ON CONFLICT DO NOTHING",
            course_id,
        )
    await pool.execute(
        "INSERT INTO lessons (lesson_id, course_id, title, position) VALUES ($1, $2, 'Lesson', 1) "
        "ON CONFLICT DO NOTHING",
        lesson_id,
        course_id,
    )


async def _get_or_create_user_row(pool, app, telegram_id):
    """Trigger current_user's auto-create via a request, then fetch the row."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/tasks/active", headers=_auth_headers(telegram_id))
    return await pool.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)


async def _set_credentials(pool, user_id):
    await pool.execute(
        "UPDATE users SET unix_email = 'a@b.com', unix_password_enc = 'enc' WHERE id = $1",
        user_id,
    )


async def _grant_credits(pool, user_id, amount=5):
    await ledger.add_entry(pool, user_id=user_id, delta=amount, reason="bonus")


async def test_watch_without_credentials_returns_409(pool):
    telegram_id = 910001
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _grant_credits(pool, user["id"])
    await _seed_lesson(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/watch", json={"type": "lesson", "id": "l1"}, headers=_auth_headers(telegram_id)
        )

    assert resp.status_code == 409
    assert resp.json() == {"error": "no_credentials"}


async def test_watch_without_credits_returns_402(pool):
    telegram_id = 910002
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _set_credentials(pool, user["id"])
    await _seed_lesson(pool)
    # welcome credits already granted balance > 0 for a fresh user in real
    # flow via current_user, but here we bypass that by not calling it before
    # granting -- ensure balance is exactly 0 by not adding anything and by
    # accounting for whatever get_or_create_user's dependency already gave.
    balance = await ledger.get_balance(pool, user["id"])
    if balance > 0:
        await ledger.add_entry(pool, user_id=user["id"], delta=-balance, reason="watch")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/watch", json={"type": "lesson", "id": "l1"}, headers=_auth_headers(telegram_id)
        )

    assert resp.status_code == 402
    assert resp.json() == {"error": "no_credits"}


async def test_watch_success_enqueues_task_201(pool):
    telegram_id = 910003
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _set_credentials(pool, user["id"])
    await _grant_credits(pool, user["id"])
    await _seed_lesson(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/watch", json={"type": "lesson", "id": "l1"}, headers=_auth_headers(telegram_id)
        )

    assert resp.status_code == 201
    body = resp.json()
    assert "task_id" in body

    row = await pool.fetchrow("SELECT * FROM watch_tasks WHERE id = $1", body["task_id"])
    assert row["type"] == "lesson"
    assert row["lesson_id"] == "l1"
    assert row["status"] == "queued"


async def test_watch_duplicate_active_task_returns_409(pool):
    telegram_id = 910004
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _set_credentials(pool, user["id"])
    await _grant_credits(pool, user["id"])
    await _seed_lesson(pool, lesson_id="l1")
    await _seed_lesson(pool, lesson_id="l2")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/watch", json={"type": "lesson", "id": "l1"}, headers=_auth_headers(telegram_id)
        )
        assert first.status_code == 201

        second = await client.post(
            "/watch", json={"type": "lesson", "id": "l2"}, headers=_auth_headers(telegram_id)
        )

    assert second.status_code == 409
    assert second.json() == {"error": "task_already_active"}


async def test_get_active_task_empty_returns_204(pool):
    telegram_id = 910005
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/active", headers=_auth_headers(telegram_id))

    assert resp.status_code == 204
    assert resp.content == b""


async def test_get_active_task_happy_path(pool):
    telegram_id = 910006
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _seed_lesson(pool)
    task_id = await tasks.enqueue(pool, user_id=user["id"], type="lesson", lesson_id="l1")
    await tasks.update_progress(pool, task_id, {"current": 1, "total": 3})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/active", headers=_auth_headers(telegram_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(task_id)
    assert body["status"] == "queued"
    assert body["progress"] == {"current": 1, "total": 3}


async def test_cancel_queued_task_sets_cancelled(pool):
    telegram_id = 910007
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _seed_lesson(pool)
    task_id = await tasks.enqueue(pool, user_id=user["id"], type="lesson", lesson_id="l1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/cancel", headers=_auth_headers(telegram_id))

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    row = await pool.fetchrow("SELECT status FROM watch_tasks WHERE id = $1", task_id)
    assert row["status"] == "cancelled"


async def test_cancel_running_task_sets_cancel_requested_without_clobbering_progress(pool):
    telegram_id = 910008
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _seed_lesson(pool)
    task_id = await tasks.enqueue(pool, user_id=user["id"], type="lesson", lesson_id="l1")
    await tasks.claim_next(pool)  # -> running
    await tasks.update_progress(pool, task_id, {"current": 2, "total": 5})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/cancel", headers=_auth_headers(telegram_id))

    assert resp.status_code == 200
    body = resp.json()
    # status must NOT flip to cancelled directly for a running task -- the
    # worker owns that transition, this only requests it.
    assert body["status"] == "running"
    assert body["progress"]["cancel_requested"] is True
    assert body["progress"]["current"] == 2
    assert body["progress"]["total"] == 5

    row = await pool.fetchrow("SELECT status FROM watch_tasks WHERE id = $1", task_id)
    assert row["status"] == "running"


async def test_cancel_someone_elses_task_rejected(pool):
    owner_tid = 910009
    other_tid = 910010
    app = _make_app(pool)
    owner = await _get_or_create_user_row(pool, app, owner_tid)
    await _get_or_create_user_row(pool, app, other_tid)
    await _seed_lesson(pool)
    task_id = await tasks.enqueue(pool, user_id=owner["id"], type="lesson", lesson_id="l1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/cancel", headers=_auth_headers(other_tid))

    assert resp.status_code == 404

    row = await pool.fetchrow("SELECT status FROM watch_tasks WHERE id = $1", task_id)
    assert row["status"] == "queued"


async def test_cancel_terminal_task_is_noop_200(pool):
    telegram_id = 910011
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)
    await _seed_lesson(pool)
    task_id = await tasks.enqueue(pool, user_id=user["id"], type="lesson", lesson_id="l1")
    await tasks.finish(pool, task_id, "done")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/cancel", headers=_auth_headers(telegram_id))

    assert resp.status_code == 200
    assert resp.json()["status"] == "done"

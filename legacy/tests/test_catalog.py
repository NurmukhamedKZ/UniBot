"""Tests for apps/api/routers/catalog.py: courses/lessons/transcribe endpoints.

Follows the exact test-app-with-valid-initData pattern from tests/test_auth.py:
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

from apps.api.routers.catalog import router as catalog_router
from packages.db import tasks

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
    app.include_router(catalog_router)
    app.state.pool = pool
    return app


@pytest.fixture(autouse=True)
def _set_bot_token(monkeypatch):
    monkeypatch.setattr("apps.api.auth.TELEGRAM_TOKEN", TEST_BOT_TOKEN)


@pytest.fixture
def auth_headers():
    telegram_id = 900001
    init_data = _build_init_data(TEST_BOT_TOKEN, {"id": telegram_id, "username": "stu"})
    return {"X-Telegram-Init-Data": init_data}


async def _seed_course_with_lessons(pool):
    await pool.execute(
        "INSERT INTO courses (course_id, title, cover, lessons_count) "
        "VALUES ('c1', 'Course One', 'cover.png', 2)"
    )
    await pool.execute(
        "INSERT INTO lessons (lesson_id, course_id, title, position) "
        "VALUES ('l1', 'c1', 'Lesson One', 1), ('l2', 'c1', 'Lesson Two', 2)"
    )
    await pool.execute(
        "INSERT INTO transcripts (lesson_id, text) VALUES ('l1', 'transcript text')"
    )
    await pool.execute(
        "INSERT INTO questions (lesson_id, question, answers, llm_answer_index) "
        "VALUES ('l1', 'Q1?', '[\"a\",\"b\",\"c\",\"d\"]'::jsonb, 1), "
        "('l1', 'Q2?', '[\"a\",\"b\",\"c\",\"d\"]'::jsonb, 2)"
    )


async def test_get_courses_returns_seeded_courses(pool, auth_headers):
    await _seed_course_with_lessons(pool)
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/courses", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["course_id"] == "c1"
    assert body[0]["title"] == "Course One"


async def test_get_courses_requires_auth(pool):
    await _seed_course_with_lessons(pool)
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/courses")

    assert resp.status_code == 401


async def test_get_course_lessons_has_correct_flags(pool, auth_headers):
    await _seed_course_with_lessons(pool)
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/courses/c1", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2

    by_id = {row["lesson_id"]: row for row in body}
    assert by_id["l1"]["has_transcript"] is True
    assert by_id["l1"]["questions_count"] == 2
    assert by_id["l2"]["has_transcript"] is False
    assert by_id["l2"]["questions_count"] == 0


async def test_get_lesson_content_returns_transcript_and_questions(pool, auth_headers):
    await _seed_course_with_lessons(pool)
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/lessons/l1", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Lesson One"
    assert body["transcript"] == "transcript text"
    assert len(body["questions"]) == 2
    # get_lesson_content orders by the questions.id uuid, which is not
    # insertion order — compare as a set of (question, llm_answer_index).
    by_question = {q["question"]: q for q in body["questions"]}
    assert by_question["Q1?"]["answers"] == ["a", "b", "c", "d"]
    assert by_question["Q1?"]["llm_answer_index"] == 1
    assert by_question["Q2?"]["llm_answer_index"] == 2


async def test_get_lesson_content_404_for_missing_lesson(pool, auth_headers):
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/lessons/does-not-exist", headers=auth_headers)

    assert resp.status_code == 404


async def test_transcribe_returns_existing_transcript_immediately(pool, auth_headers):
    await _seed_course_with_lessons(pool)
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/lessons/l1/transcribe", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"transcript": "transcript text"}

    # No transcribe task should have been enqueued since it was cached.
    row_count = await pool.fetchval(
        "SELECT count(*) FROM watch_tasks WHERE type = 'transcribe'"
    )
    assert row_count == 0


async def test_transcribe_enqueues_task_when_not_cached(pool, auth_headers):
    await _seed_course_with_lessons(pool)  # l2 has no transcript
    app = _make_app(pool)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/lessons/l2/transcribe", headers=auth_headers)

    assert resp.status_code == 202
    body = resp.json()
    assert "task_id" in body

    task_row = await pool.fetchrow(
        "SELECT * FROM watch_tasks WHERE id = $1", body["task_id"]
    )
    assert task_row is not None
    assert task_row["type"] == "transcribe"
    assert task_row["lesson_id"] == "l2"


async def test_transcribe_enqueue_never_hits_active_task_unique_index(pool, auth_headers):
    """The partial unique index only restricts type IN ('lesson','course') —
    two transcribe tasks for the same user should both enqueue fine even
    while a lesson/course task is also active for that user."""
    await _seed_course_with_lessons(pool)
    app = _make_app(pool)

    telegram_id = 900001
    user_row = await pool.fetchrow("SELECT id FROM users WHERE telegram_id = $1", telegram_id)
    if user_row is None:
        # user gets created lazily by the first authenticated request; make one first.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/courses", headers=auth_headers)
        user_row = await pool.fetchrow("SELECT id FROM users WHERE telegram_id = $1", telegram_id)

    await tasks.enqueue(pool, user_id=user_row["id"], type="lesson", lesson_id="l1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/lessons/l2/transcribe", headers=auth_headers)

    assert resp.status_code == 202

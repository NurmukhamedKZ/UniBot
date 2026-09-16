"""apps/api/routers/uninet.py + me.py поверх фейковой uninet-сессии (без сети)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from packages.core import uninet
from packages.db import catalog, tasks

pytestmark = pytest.mark.asyncio

TOKEN = "123456:test-bot-token"
TID = 777001


def _init_data() -> str:
    data = {"user": json.dumps({"id": TID}), "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class FakeUninet:
    def __init__(self):
        self.marked: list[int] = []
        self.weeks = {
            1: {"week": {"id": 1}, "content": [{"videos": [
                {"id": 10, "title": "A", "url": "https://kinescope.io/aaaaaaaaaa", "durationSec": 60, "completed": True},
                {"id": 11, "title": "B", "url": "https://kinescope.io/bbbbbbbbbb", "durationSec": 90, "completed": False},
            ]}]},
            2: {"week": {"id": 2}, "content": [{"videos": [
                {"id": 20, "title": "C", "url": "https://kinescope.io/cccccccccc", "durationSec": 30, "completed": False},
            ]}]},
        }

    async def login(self):
        pass

    async def courses(self):
        return [{"id": 55, "code": "CS101", "name": "Intro", "teacher": "T", "department": "D"}]

    async def course(self, course_id):
        return {"course": {"id": course_id}, "weeks": [
            {"id": 1, "unlocked": True}, {"id": 2, "unlocked": True}, {"id": 3, "unlocked": False},
        ]}

    async def week(self, week_id):
        return self.weeks[week_id]

    async def mark_watched(self, video):
        self.marked.append(video["id"])


@pytest.fixture
async def client(pool, monkeypatch):
    monkeypatch.setattr("apps.api.auth.TELEGRAM_TOKEN", TOKEN)
    fake = FakeUninet()
    monkeypatch.setattr(uninet, "session", lambda email, password: fake)
    app.state.pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-Telegram-Init-Data": _init_data()}) as c:
        c.fake = fake
        yield c


async def test_no_credentials_409(client):
    resp = await client.get("/weeks/1")
    assert resp.status_code == 409
    assert resp.json() == {"error": "no_credentials"}


async def test_bad_credentials_rejected_on_save(client, monkeypatch):
    async def bad_login():
        raise uninet.InvalidCredentials()

    monkeypatch.setattr(client.fake, "login", bad_login)
    resp = await client.put("/me/credentials", json={"email": "s@kbtu.kz", "password": "nope"})
    assert resp.status_code == 409
    assert resp.json() == {"error": "bad_credentials"}
    assert (await client.get("/me")).json()["credentials_set"] is False


async def test_week_and_watch(client, pool):
    assert (await client.put("/me/credentials", json={"email": "s@kbtu.kz", "password": "pw"})).status_code == 200
    await catalog.save_transcript(pool, "aaaaaaaaaa", "text")

    week = (await client.get("/weeks/1")).json()
    assert [(v["kinescope_id"], v["completed"], v["has_transcript"]) for v in week["videos"]] == [
        ("aaaaaaaaaa", True, True), ("bbbbbbbbbb", False, False),
    ]

    assert (await client.post("/weeks/1/watch")).json() == {"marked": 1}
    assert client.fake.marked == [11]  # уже просмотренное не трогаем

    client.fake.marked.clear()
    assert (await client.post("/courses/55/watch")).json() == {"marked": 2}
    assert client.fake.marked == [11, 20]  # закрытая неделя 3 пропущена


async def test_transcribe_cached_enqueue_and_dedup(client, pool):
    await catalog.save_transcript(pool, "aaaaaaaaaa", "ready")

    resp = await client.post("/videos/aaaaaaaaaa/transcribe")
    assert resp.status_code == 200 and resp.json()["transcript"] == "ready"

    assert (await client.post("/videos/bbbbbbbbbb/transcribe")).status_code == 409  # новые — только с кредами
    await client.put("/me/credentials", json={"email": "s@kbtu.kz", "password": "pw"})

    first = await client.post("/videos/bbbbbbbbbb/transcribe")
    second = await client.post("/videos/bbbbbbbbbb/transcribe")
    assert first.status_code == second.status_code == 202
    assert second.json()["status"] == "queued"
    assert await pool.fetchval("SELECT count(*) FROM watch_tasks WHERE lesson_id = 'bbbbbbbbbb'") == 1

    task = await tasks.claim_next(pool)
    await tasks.finish(pool, task["id"], "failed", error="boom")
    assert (await client.get("/videos/bbbbbbbbbb/transcript")).json() == {"transcript": None, "status": "failed"}
    await client.post("/videos/bbbbbbbbbb/transcribe")  # после провала можно перезапустить
    assert await pool.fetchval("SELECT count(*) FROM watch_tasks WHERE lesson_id = 'bbbbbbbbbb'") == 2

    assert (await client.get("/videos/bad!id/transcript")).status_code == 404


async def test_anonymous_reads_cached_catalog(client, pool):
    assert (await client.get("/courses")).json() == []
    await client.put("/me/credentials", json={"email": "s@kbtu.kz", "password": "pw"})
    await client.get("/courses")
    await client.get("/courses/55")
    await client.get("/weeks/1")  # неделю 2 никто не открывал
    await catalog.save_transcript(pool, "aaaaaaaaaa", "text")
    await client.delete("/me/credentials")

    assert [c["id"] for c in (await client.get("/courses")).json()] == [55]
    assert [w["id"] for w in (await client.get("/courses/55")).json()["weeks"]] == [1]
    week = (await client.get("/weeks/1")).json()
    assert [(v["kinescope_id"], v["completed"], v["has_transcript"]) for v in week["videos"]] == [
        ("aaaaaaaaaa", False, True), ("bbbbbbbbbb", False, False),
    ]
    assert (await client.get("/weeks/2")).json() == {"error": "no_credentials"}
    assert (await client.get("/courses/99")).status_code == 409
    assert (await client.post("/weeks/1/watch")).status_code == 409

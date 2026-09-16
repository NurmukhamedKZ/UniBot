"""Tests for apps/api/auth.py: initData HMAC validation + the current_user dependency.

Mounts a minimal FastAPI app with a trivial endpoint behind ``current_user``, wiring
``app.state.pool`` directly to the test ``pool`` fixture (same real local
Postgres every other packages/db test uses) instead of going through the
lifespan's own pool creation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from apps.api.auth import current_user

pytestmark = pytest.mark.asyncio

TEST_BOT_TOKEN = "123456:test-bot-token"


def _build_init_data(
    bot_token: str,
    user: dict,
    auth_date: int | None = None,
    start_param: str | None = None,
    bad_hash: bool = False,
) -> str:
    """Build a valid (or deliberately tampered) Telegram WebApp initData string."""
    data = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAtestquery",
    }
    if start_param is not None:
        data["start_param"] = start_param

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

    if bad_hash:
        computed_hash = "0" * 64

    data["hash"] = computed_hash
    return urlencode(data)


def _make_app():
    """Minimal app mounting current_user; app.state.pool set by the caller."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user=Depends(current_user)):
        return {"id": str(user["id"]), "telegram_id": user["telegram_id"]}

    return app


@pytest.fixture(autouse=True)
def _set_bot_token(monkeypatch):
    monkeypatch.setattr("apps.api.auth.TELEGRAM_TOKEN", TEST_BOT_TOKEN)


async def test_bad_hash_401(pool):
    app = _make_app()
    app.state.pool = pool

    init_data = _build_init_data(TEST_BOT_TOKEN, {"id": 555001, "username": "eve"}, bad_hash=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoami", headers={"X-Telegram-Init-Data": init_data})

    assert resp.status_code == 401


async def test_missing_header_401(pool):
    app = _make_app()
    app.state.pool = pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoami")

    assert resp.status_code == 401


async def test_valid_initdata_creates_user(pool):

    app = _make_app()
    app.state.pool = pool

    telegram_id = 555002
    init_data = _build_init_data(
        TEST_BOT_TOKEN, {"id": telegram_id, "username": "alice", "first_name": "Alice"}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoami", headers={"X-Telegram-Init-Data": init_data})

    assert resp.status_code == 200
    body = resp.json()
    assert body["telegram_id"] == telegram_id

    row = await pool.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
    assert row is not None




async def test_expired_initdata_401(pool):
    app = _make_app()
    app.state.pool = pool

    stale_auth_date = int(time.time()) - 7200  # 2h old, default max_age is 1h
    init_data = _build_init_data(
        TEST_BOT_TOKEN, {"id": 555004, "username": "carl"}, auth_date=stale_auth_date
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoami", headers={"X-Telegram-Init-Data": init_data})

    assert resp.status_code == 401



async def test_malformed_auth_date_401_not_500(pool):
    """A non-numeric auth_date (only reachable past a valid HMAC in practice)
    must 401, not raise an uncaught ValueError -> 500."""
    app = _make_app()
    app.state.pool = pool

    data = {
        "user": json.dumps({"id": 555009, "username": "gina"}, separators=(",", ":")),
        "auth_date": "not-a-number",
        "query_id": "AAtestquery",
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode(data)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoami", headers={"X-Telegram-Init-Data": init_data})

    assert resp.status_code == 401


async def test_malformed_user_payload_401_not_500(pool):
    """A ``user`` field that parses as JSON but lacks an ``id`` key (only
    reachable past a valid HMAC in practice) must 401, not raise an uncaught
    KeyError -> 500."""
    app = _make_app()
    app.state.pool = pool

    data = {
        "user": json.dumps({"username": "no-id-here"}, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "AAtestquery",
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode(data)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoami", headers={"X-Telegram-Init-Data": init_data})

    assert resp.status_code == 401

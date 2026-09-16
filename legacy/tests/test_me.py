"""Tests for apps/api/routers/me.py: GET /me, unix-credentials, GET /referral.

Follows the same test-app-with-valid-initData pattern as tests/test_watch.py
and tests/test_catalog.py: mount a fresh FastAPI app with the real router,
wire ``app.state.pool`` directly to the ``pool`` fixture (real local
Postgres), and build valid Telegram initData by hand with the same HMAC
scheme ``apps.api.auth`` expects.
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

from apps.api.routers.me import router as me_router
from packages.db import ledger
from packages.shared.crypto import decrypt_password

pytestmark = pytest.mark.asyncio

TEST_BOT_TOKEN = "123456:test-bot-token"


def _build_init_data(bot_token: str, user: dict, start_param: str | None = None) -> str:
    data = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "AAtestquery",
    }
    if start_param is not None:
        data["start_param"] = start_param
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def _make_app(pool):
    app = FastAPI()
    app.include_router(me_router)
    app.state.pool = pool
    return app


@pytest.fixture(autouse=True)
def _set_bot_token(monkeypatch):
    monkeypatch.setattr("apps.api.auth.TELEGRAM_TOKEN", TEST_BOT_TOKEN)


@pytest.fixture(autouse=True)
def _set_bot_username(monkeypatch):
    monkeypatch.setattr("apps.api.routers.me.BOT_USERNAME", "TestUniBot")


def _auth_headers(telegram_id: int) -> dict:
    init_data = _build_init_data(TEST_BOT_TOKEN, {"id": telegram_id, "username": "stu"})
    return {"X-Telegram-Init-Data": init_data}


async def _get_or_create_user_row(pool, app, telegram_id, start_param=None):
    headers = {
        "X-Telegram-Init-Data": _build_init_data(
            TEST_BOT_TOKEN, {"id": telegram_id, "username": "stu"}, start_param=start_param
        )
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/me", headers=headers)
    return await pool.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)


async def test_get_me_happy_path(pool):
    telegram_id = 920001
    app = _make_app(pool)
    user = await _get_or_create_user_row(pool, app, telegram_id)

    # welcome credits already granted; add a purchase entry too
    await ledger.add_entry(pool, user_id=user["id"], delta=10, reason="purchase")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/me", headers=_auth_headers(telegram_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["balance"] == await ledger.get_balance(pool, user["id"])
    assert body["unix_email_set"] is False
    assert isinstance(body["history"], list)
    assert len(body["history"]) >= 2  # welcome bonus + purchase
    assert isinstance(body["watched"], list)
    assert body["watched"] == []


async def test_unix_credentials_put_delete_round_trip(pool):
    telegram_id = 920002
    app = _make_app(pool)
    await _get_or_create_user_row(pool, app, telegram_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        put_resp = await client.put(
            "/me/unix-credentials",
            headers=_auth_headers(telegram_id),
            json={"email": "student@uni-x.almv.kz", "password": "hunter2"},
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["unix_email_set"] is True

        row = await pool.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )
        assert row["unix_email"] == "student@uni-x.almv.kz"
        assert row["unix_password_enc"] != "hunter2"  # actually encrypted
        assert decrypt_password(row["unix_password_enc"]) == "hunter2"

        me_resp = await client.get("/me", headers=_auth_headers(telegram_id))
        assert me_resp.json()["unix_email_set"] is True

        del_resp = await client.delete(
            "/me/unix-credentials", headers=_auth_headers(telegram_id)
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["unix_email_set"] is False

        row = await pool.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )
        assert row["unix_email"] is None
        assert row["unix_password_enc"] is None


async def test_referral_stats(pool, make_user):
    referrer_tid = 920010
    referrer_id = await make_user(referrer_tid)

    app = _make_app(pool)
    # Two referred users sign up with start_param=ref_<referrer_tid>
    await _get_or_create_user_row(pool, app, 920011, start_param=f"ref_{referrer_tid}")
    await _get_or_create_user_row(pool, app, 920012, start_param=f"ref_{referrer_tid}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/referral", headers=_auth_headers(referrer_tid))

    assert resp.status_code == 200
    body = resp.json()
    assert body["link"] == f"https://t.me/TestUniBot?startapp=ref_{referrer_tid}"
    assert body["invited"] == 2
    assert body["credited"] == 0  # neither referred user has watched yet

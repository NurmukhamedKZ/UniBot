import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.api.routers.payments import TRIBUTE_SIGNATURE_HEADER
from apps.api.routers.payments import router as payments_router
from packages.db import ledger, payments

pytestmark = pytest.mark.asyncio


async def test_duplicate_payment_not_double_credited(pool, make_user):
    user_id = await make_user()
    external_id = f"charge_{uuid.uuid4()}"

    first = await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="tribute",
        external_id=external_id,
        package="S",
        credits=10,
        amount=1490,
        currency="KZT",
        raw={"ok": True},
    )
    second = await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="tribute",
        external_id=external_id,
        package="S",
        credits=10,
        amount=1490,
        currency="KZT",
        raw={"ok": True},
    )

    assert first is True
    assert second is False
    assert await ledger.get_balance(pool, user_id) == 10


async def test_different_external_ids_both_credit(pool, make_user):
    user_id = await make_user()

    await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="stars",
        external_id=f"charge_{uuid.uuid4()}",
        package="S",
        credits=10,
        amount=250,
        currency="XTR",
        raw={},
    )
    await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="stars",
        external_id=f"charge_{uuid.uuid4()}",
        package="M",
        credits=50,
        amount=400,
        currency="XTR",
        raw={},
    )

    assert await ledger.get_balance(pool, user_id) == 60


# --- Router-level tests: apps/api/routers/payments.py -----------------------

TRIBUTE_SECRET = "test-tribute-secret"


def _make_app(pool):
    app = FastAPI()
    app.include_router(payments_router)
    app.state.pool = pool
    return app


@pytest.fixture(autouse=True)
def _set_tribute_secret(monkeypatch):
    monkeypatch.setattr("apps.api.routers.payments.TRIBUTE_WEBHOOK_SECRET", TRIBUTE_SECRET)


def _sign(body: bytes, secret: str = TRIBUTE_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _FakeCreateInvoiceLinkClient:
    """Stands in for httpx.AsyncClient, faking a successful Bot API response."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, timeout=None):
        assert "createInvoiceLink" in url
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"ok": True, "result": "https://t.me/invoice/fake123"},
            request=request,
        )


async def test_stars_create_invoice(pool, monkeypatch):
    monkeypatch.setattr("apps.api.auth.TELEGRAM_TOKEN", "123456:test-bot-token")
    monkeypatch.setattr(
        "apps.api.routers.payments.TELEGRAM_TOKEN", "123456:test-bot-token"
    )
    monkeypatch.setattr(httpx, "AsyncClient", _FakeCreateInvoiceLinkClient)

    app = _make_app(pool)

    telegram_id = 930001
    init_data_headers = {
        "X-Telegram-Init-Data": _build_stars_init_data(telegram_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/payments/stars/create-invoice",
            headers=init_data_headers,
            json={"package": "S"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"invoice_url": "https://t.me/invoice/fake123"}


async def test_stars_create_invoice_rejects_unknown_package(pool, monkeypatch):
    monkeypatch.setattr("apps.api.auth.TELEGRAM_TOKEN", "123456:test-bot-token")

    app = _make_app(pool)
    telegram_id = 930002
    headers = {"X-Telegram-Init-Data": _build_stars_init_data(telegram_id)}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/payments/stars/create-invoice", headers=headers, json={"package": "XL"}
        )

    assert resp.status_code == 422


def _build_stars_init_data(telegram_id: int) -> str:
    import time
    from urllib.parse import urlencode

    bot_token = "123456:test-bot-token"
    data = {
        "user": json.dumps({"id": telegram_id, "username": "stu"}, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "AAtestquery",
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


async def test_tribute_webhook_duplicate_is_noop(pool, make_user):
    telegram_id = 940001
    await make_user(telegram_id)

    app = _make_app(pool)
    body = json.dumps(
        {
            "id": f"tribute_{uuid.uuid4()}",
            "package": "M",
            "telegram_id": telegram_id,
            "amount": 2490,
            "currency": "KZT",
        }
    ).encode()
    headers = {
        TRIBUTE_SIGNATURE_HEADER: _sign(body),
        "Content-Type": "application/json",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/payments/tribute/webhook", content=body, headers=headers)
        second = await client.post("/payments/tribute/webhook", content=body, headers=headers)

    assert first.status_code == 200
    assert first.json()["credited"] is True
    assert second.status_code == 200
    assert second.json()["credited"] is False

    user_id = await pool.fetchval(
        "SELECT id FROM users WHERE telegram_id = $1", telegram_id
    )
    assert await ledger.get_balance(pool, user_id) == 50  # package M credits, credited once


async def test_tribute_webhook_bad_signature_rejected(pool, make_user):
    telegram_id = 940002
    await make_user(telegram_id)

    app = _make_app(pool)
    body = json.dumps(
        {
            "id": f"tribute_{uuid.uuid4()}",
            "package": "S",
            "telegram_id": telegram_id,
            "amount": 1490,
            "currency": "KZT",
        }
    ).encode()
    headers = {
        TRIBUTE_SIGNATURE_HEADER: "0" * 64,  # wrong signature
        "Content-Type": "application/json",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/payments/tribute/webhook", content=body, headers=headers)

    assert resp.status_code == 401

    user_id = await pool.fetchval(
        "SELECT id FROM users WHERE telegram_id = $1", telegram_id
    )
    assert await ledger.get_balance(pool, user_id) == 0  # no credit applied


async def test_tribute_webhook_missing_signature_header_rejected(pool, make_user):
    telegram_id = 940003
    await make_user(telegram_id)

    app = _make_app(pool)
    body = json.dumps(
        {"id": f"tribute_{uuid.uuid4()}", "package": "S", "telegram_id": telegram_id}
    ).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/payments/tribute/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 401

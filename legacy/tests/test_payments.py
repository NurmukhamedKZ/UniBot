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

TRIBUTE_SECRET = "test-tribute-api-key"
TRIBUTE_PRODUCT_IDS = {"S": 111, "M": 222, "L": 333}


def _make_app(pool):
    app = FastAPI()
    app.include_router(payments_router)
    app.state.pool = pool
    return app


@pytest.fixture(autouse=True)
def _set_tribute_secret(monkeypatch):
    monkeypatch.setattr("apps.api.routers.payments.TRIBUTE_API_KEY", TRIBUTE_SECRET)
    monkeypatch.setattr(
        "apps.api.routers.payments.TRIBUTE_PACKAGE_BY_PRODUCT_ID",
        {v: k for k, v in TRIBUTE_PRODUCT_IDS.items()},
    )


def _sign(body: bytes, secret: str = TRIBUTE_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _digital_product_event(*, product_id, purchase_id, telegram_id, amount=None, currency="usd"):
    """Build a Tribute `new_digital_product` webhook envelope (real shape,
    confirmed via Tribute's OpenAPI spec: {name, payload: {...}})."""
    return {
        "name": "new_digital_product",
        "payload": {
            "product_id": product_id,
            "purchase_id": purchase_id,
            "transaction_id": purchase_id * 10,
            "telegram_user_id": telegram_id,
            "amount": amount if amount is not None else 500,
            "currency": currency,
        },
    }


class _FakeCreateInvoiceLinkClient:
    """Stands in for httpx.AsyncClient, faking a successful Bot API response.

    Captures the request body/params sent to ``createInvoiceLink`` on
    ``self.captured`` (class-level, since the router constructs a fresh
    instance per call via ``async with httpx.AsyncClient() as client``) so
    tests can assert the exact Stars invariants: XTR currency, empty-string
    provider_token, and an unmultiplied stars price.
    """

    captured: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, timeout=None):
        assert "createInvoiceLink" in url
        type(self).captured = json
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
    _FakeCreateInvoiceLinkClient.captured = None
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

    # Lock in the Stars invariants: XTR currency, empty-string provider_token
    # (Stars payments require no real payment provider), and the price amount
    # matching PACKAGES["S"]["stars"] exactly (no multiplication/conversion).
    from packages.shared.config import PACKAGES

    sent = _FakeCreateInvoiceLinkClient.captured
    assert sent is not None
    assert sent["currency"] == "XTR"
    assert sent["provider_token"] == ""
    assert sent["prices"] == [{"label": "Пакет S", "amount": PACKAGES["S"]["stars"]}]


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
        _digital_product_event(
            product_id=TRIBUTE_PRODUCT_IDS["M"],
            purchase_id=int(uuid.uuid4().int % 1_000_000),
            telegram_id=telegram_id,
        )
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
    assert await ledger.get_balance(pool, user_id) == 40  # package M credits, credited once


async def test_tribute_webhook_bad_signature_rejected(pool, make_user):
    telegram_id = 940002
    await make_user(telegram_id)

    app = _make_app(pool)
    body = json.dumps(
        _digital_product_event(
            product_id=TRIBUTE_PRODUCT_IDS["S"],
            purchase_id=int(uuid.uuid4().int % 1_000_000),
            telegram_id=telegram_id,
        )
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


async def test_tribute_webhook_non_dict_payload_is_graceful(pool, make_user):
    """A JSON list/scalar body (not an object) must not crash the handler.

    ``_map_tribute_product_to_package``/``_resolve_user_reference`` call
    ``payload.get(...)``, which doesn't exist on list/scalar JSON values --
    without the isinstance guard this raised an unhandled AttributeError
    (500) instead of the graceful 200 path used for other malformed
    payloads.
    """
    body = json.dumps(["not", "an", "object"]).encode()
    headers = {
        TRIBUTE_SIGNATURE_HEADER: _sign(body),
        "Content-Type": "application/json",
    }

    app = _make_app(pool)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/payments/tribute/webhook", content=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "malformed_payload"}


async def test_tribute_webhook_non_purchase_event_ignored(pool, make_user):
    """newSubscription/newDonation/etc. land on the same URL as package
    purchases -- must be a no-op 200, not treated as an unparseable payload."""
    body = json.dumps({"name": "new_subscription", "payload": {"foo": "bar"}}).encode()
    headers = {
        TRIBUTE_SIGNATURE_HEADER: _sign(body),
        "Content-Type": "application/json",
    }

    app = _make_app(pool)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/payments/tribute/webhook", content=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "reason": "ignored_event", "event": "new_subscription"}


async def test_tribute_webhook_missing_signature_header_rejected(pool, make_user):
    telegram_id = 940003
    await make_user(telegram_id)

    app = _make_app(pool)
    body = json.dumps(
        _digital_product_event(
            product_id=TRIBUTE_PRODUCT_IDS["S"],
            purchase_id=int(uuid.uuid4().int % 1_000_000),
            telegram_id=telegram_id,
        )
    ).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/payments/tribute/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 401

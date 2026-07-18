"""Payment endpoints: Telegram Stars invoice creation + the Tribute webhook.

Per docs/plan.md Task 10. ``POST /payments/stars/create-invoice`` sits behind
``current_user`` like every other router. ``POST /payments/tribute/webhook``
does NOT — Tribute calls it directly from the internet, not through Telegram,
so there is no initData to validate. It authenticates via an HMAC signature
header instead (see ``_verify_tribute_signature`` below).

IMPORTANT — underspecified Tribute contract: docs/plan.md only says
"проверка подписи TRIBUTE_WEBHOOK_SECRET (HMAC заголовка по документации
Tribute)" without naming the exact header, algorithm, or payload field
names. Everything below marked "ASSUMPTION" is this task's best guess and
MUST be checked against Tribute's real webhook docs before this endpoint is
wired to a live Tribute product (see the Task 10 report for the full list
of judgment calls).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.api.auth import current_user
from packages.db import payments as payments_db
from packages.db import users
from packages.shared.config import PACKAGES, TELEGRAM_TOKEN, TRIBUTE_WEBHOOK_SECRET

router = APIRouter()
logger = logging.getLogger(__name__)

# ASSUMPTION: header name for Tribute's webhook signature. Tribute's real
# docs need to confirm this (candidates seen in similar Telegram-payment
# providers: "X-Tribute-Signature", "Trbt-Signature"). Update once verified.
TRIBUTE_SIGNATURE_HEADER = "X-Tribute-Signature"


def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


@router.post("/payments/stars/create-invoice")
async def create_stars_invoice(request: Request, _user=Depends(current_user)):
    body = await request.json()
    package = body.get("package")
    if package not in PACKAGES:
        raise HTTPException(422, "package must be one of: " + ", ".join(PACKAGES))

    if not TELEGRAM_TOKEN:
        raise HTTPException(500, "TELEGRAM_TOKEN is not configured")

    pkg = PACKAGES[package]
    payload = {
        "title": f"Пакет {package} — {pkg['credits']} видео",
        "description": f"{pkg['credits']} автопросмотров, не сгорают",
        "payload": f"pkg_{package}",
        "provider_token": "",
        "currency": "XTR",
        "prices": [{"label": f"Пакет {package}", "amount": pkg["stars"]}],
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createInvoiceLink",
            json=payload,
            timeout=10.0,
        )

    data = resp.json()
    if not data.get("ok"):
        logger.error("createInvoiceLink failed: %s", data)
        raise HTTPException(502, "failed to create Telegram invoice link")

    return {"invoice_url": data["result"]}


def _verify_tribute_signature(raw_body: bytes, signature: str | None) -> bool:
    """HMAC-SHA256(raw_body) over TRIBUTE_WEBHOOK_SECRET, compared to a hex header.

    ASSUMPTION (placeholder pending real Tribute docs, see module docstring):
    signature scheme is "hex(hmac_sha256(secret, raw_body))" in a header
    named ``TRIBUTE_SIGNATURE_HEADER``. If Tribute's actual scheme differs
    (different header name, base64 encoding, a signed timestamp+body
    concatenation, etc.) this function is the only place that needs to change.
    """
    if not TRIBUTE_WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(
        TRIBUTE_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _map_tribute_product_to_package(payload: dict) -> str | None:
    """Map Tribute's product/payload data to our S/M/L package key.

    ASSUMPTION (placeholder pending real Tribute docs): Tribute sends a
    field identifying which digital product was purchased. We look for a
    top-level ``package`` field first (our own product name/slug, if we
    configure Tribute products named "S"/"M"/"L" directly), falling back to
    a nested ``product.name``/``product.slug`` in case Tribute's payload
    shape nests it there instead. Whichever of these is wrong needs fixing
    once Tribute's actual webhook payload is inspected in dashboard/docs.
    """
    candidate = payload.get("package")
    if candidate in PACKAGES:
        return candidate

    product = payload.get("product") or {}
    for key in ("name", "slug", "package"):
        candidate = product.get(key)
        if candidate in PACKAGES:
            return candidate

    return None


def _resolve_user_reference(payload: dict) -> int | None:
    """Extract the Telegram user id Tribute's payload is paying on behalf of.

    ASSUMPTION (placeholder pending real Tribute docs): Tribute digital
    products can carry a custom reference/label set at checkout-link
    creation time; we assume UniBot puts the buyer's ``telegram_id`` there
    and Tribute echoes it back on the webhook as one of the fields tried
    below (``telegram_id`` top-level, or nested under ``customer``/``user``).
    This is the single most likely-to-be-wrong assumption in this file —
    Tribute's actual webhook payload must be inspected to confirm the real
    field name before going live.
    """
    for key in ("telegram_id", "telegram_user_id", "user_id"):
        value = payload.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue

    for parent_key in ("customer", "user"):
        parent = payload.get(parent_key) or {}
        for key in ("telegram_id", "id"):
            value = parent.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue

    return None


def _notify_user_of_credit(telegram_id: int, package: str, credits: int) -> None:
    """Hook point for Task 14: notify the buyer via Bot API sendMessage once
    the bot exists. Deliberately a no-op today (the plan's Phase 4 bot isn't
    built yet) — Task 14 should replace this body with an actual
    ``https://api.telegram.org/bot{TOKEN}/sendMessage`` call (or route
    through the bot's own client) using ``telegram_id``/``package``/``credits``.
    """
    return None


@router.post("/payments/tribute/webhook")
async def tribute_webhook(request: Request):
    pool = _get_pool(request)
    raw_body = await request.body()
    signature = request.headers.get(TRIBUTE_SIGNATURE_HEADER)

    if not _verify_tribute_signature(raw_body, signature):
        # JUDGMENT CALL (documented in the Task 10 report): the brief's own
        # text only mandates "always 200 on duplicate", not "always 200 on
        # bad signature". Silently 200-ing a payload that failed signature
        # verification would let anyone who discovers this URL credit
        # arbitrary users for free by replaying/forging a payload -- a much
        # bigger risk than Tribute retry-storming a 401. So: 401 here, 200
        # is reserved for "valid signature, whether or not it was a
        # duplicate/no-op".
        logger.warning("tribute webhook: signature verification failed")
        raise HTTPException(401, "invalid signature")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON body")

    package = _map_tribute_product_to_package(payload)
    external_id = payload.get("id") or payload.get("payment_id") or payload.get("external_id")
    telegram_id = _resolve_user_reference(payload)

    if package is None or external_id is None or telegram_id is None:
        logger.error(
            "tribute webhook: could not parse payload (package=%s external_id=%s telegram_id=%s)",
            package,
            external_id,
            telegram_id,
        )
        # Signature was valid but we can't act on this payload. Still 200 --
        # returning an error status here would make Tribute retry a payload
        # we will never be able to parse differently, and we've already
        # logged it for manual follow-up.
        return JSONResponse(status_code=200, content={"ok": False, "reason": "unparseable_payload"})

    user_id = await users.get_id_by_telegram_id(pool, telegram_id)
    if user_id is None:
        logger.error("tribute webhook: unknown telegram_id=%s (external_id=%s)", telegram_id, external_id)
        return JSONResponse(status_code=200, content={"ok": False, "reason": "unknown_user"})

    pkg = PACKAGES[package]
    amount = payload.get("amount", pkg["price_kzt"])
    currency = payload.get("currency", "KZT")

    credited = await payments_db.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="tribute",
        external_id=str(external_id),
        package=package,
        credits=pkg["credits"],
        amount=amount,
        currency=currency,
        raw=payload,
    )

    if credited:
        _notify_user_of_credit(telegram_id, package, pkg["credits"])

    # Always 200 once we get here: whether this was a fresh credit or a
    # duplicate no-op, Tribute should not retry.
    return {"ok": True, "credited": credited}

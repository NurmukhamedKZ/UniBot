"""Payment endpoints: Telegram Stars invoice creation + the Tribute webhook.

Per docs/plan.md Task 10. ``POST /payments/stars/create-invoice`` sits behind
``current_user`` like every other router. ``POST /payments/tribute/webhook``
does NOT — Tribute calls it directly from the internet, not through Telegram,
so there is no initData to validate. It authenticates via an HMAC signature
header instead (see ``_verify_tribute_signature`` below).

Contract confirmed against Tribute's real docs (2026-08-17):
https://wiki.tribute.tg/for-content-creators/api-documentation/webhooks and
the OpenAPI spec at https://tribute.tg/api/v1/openapi/en. Remaining
uncertainty: the docs say the signature is "HMAC-SHA256 ... signed with your
API key" but don't state hex vs base64 encoding — hex is assumed (the more
common convention). Verify against a real test delivery from the Tribute
dashboard before going live; that's the one thing that could still be wrong
here.
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
from packages.shared.config import (
    PACKAGES,
    TELEGRAM_TOKEN,
    TRIBUTE_API_KEY,
    TRIBUTE_PACKAGE_BY_PRODUCT_ID,
)

router = APIRouter()
logger = logging.getLogger(__name__)

TRIBUTE_SIGNATURE_HEADER = "trbt-signature"
# The only event type that credits a package purchase; Tribute also sends
# subscription/donation/physical-order events on the same endpoint, which we
# ignore (UniBot only sells one-off digital products, not subscriptions).
TRIBUTE_DIGITAL_PRODUCT_EVENT = "new_digital_product"


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
    """hex(HMAC-SHA256(raw_body)) signed with the Tribute API key.

    Confirmed: header ``trbt-signature``, HMAC-SHA256 over the raw request
    body, secret = the author's API key (Tribute has no separate "webhook
    secret"). Hex encoding is assumed — the docs don't say hex vs base64.
    """
    if not TRIBUTE_API_KEY or not signature:
        return False
    expected = hmac.new(TRIBUTE_API_KEY.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


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
        # ponytail: TEMP DEBUG -- hex vs base64 encoding of trbt-signature is
        # unconfirmed by Tribute's docs. Logs the received header + both
        # candidate encodings (never the API key itself) so a real Test
        # Request delivery can be diagnosed. Remove once encoding is
        # confirmed and _verify_tribute_signature is locked in.
        if TRIBUTE_API_KEY:
            import base64

            digest = hmac.new(TRIBUTE_API_KEY.encode(), raw_body, hashlib.sha256)
            logger.warning(
                "tribute webhook: signature mismatch received=%r expected_hex=%s expected_b64=%s body=%r",
                signature,
                digest.hexdigest(),
                base64.b64encode(digest.digest()).decode(),
                raw_body[:500],
            )
        else:
            logger.warning("tribute webhook: signature verification failed (TRIBUTE_API_KEY not set)")
        raise HTTPException(401, "invalid signature")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON body")

    if not isinstance(payload, dict):
        # Signature was valid but the body isn't a JSON object (e.g. a list
        # or scalar). Same graceful 200 path as the other
        # malformed/unresolvable-payload cases below, rather than an
        # unhandled AttributeError -> 500.
        logger.error("tribute webhook: payload is not a JSON object (type=%s)", type(payload).__name__)
        return JSONResponse(status_code=200, content={"ok": False, "reason": "malformed_payload"})

    event_name = payload.get("name")
    if event_name != TRIBUTE_DIGITAL_PRODUCT_EVENT:
        # Subscriptions/donations/physical orders land on this same webhook
        # URL — UniBot only sells one-off packages, so anything else is a
        # no-op, not an error.
        return JSONResponse(status_code=200, content={"ok": True, "reason": "ignored_event", "event": event_name})

    data = payload.get("payload") or {}
    package = TRIBUTE_PACKAGE_BY_PRODUCT_ID.get(data.get("product_id"))
    external_id = data.get("purchase_id")
    telegram_id = data.get("telegram_user_id")

    if package is None or external_id is None or telegram_id is None:
        logger.error(
            "tribute webhook: could not parse payload (product_id=%s package=%s external_id=%s telegram_id=%s)",
            data.get("product_id"),
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
    # Tribute docs: amount is "in smallest currency units" (e.g. cents for
    # usd) — stored raw/unconverted here, same as the rest of `raw`; nothing
    # downstream does arithmetic on it.
    amount = data.get("amount", pkg["price_kzt"])
    currency = data.get("currency", "KZT")

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

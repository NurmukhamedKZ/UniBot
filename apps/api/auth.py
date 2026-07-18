"""Telegram WebApp initData validation + the ``current_user`` FastAPI dependency.

Auth model per docs/spec.md: no JWT — every request carries the raw Telegram
WebApp ``initData`` string in the ``X-Telegram-Init-Data`` header, HMAC-validated
against ``TELEGRAM_TOKEN`` on every call, with auto-create-on-first-sight for the
``users`` row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

import asyncpg
from fastapi import Header, HTTPException, Request

from packages.db import ledger, referrals
from packages.db.users import get_or_create_user
from packages.shared.config import TELEGRAM_TOKEN, WELCOME_CREDITS


def validate_init_data(init_data: str, bot_token: str, max_age: int = 3600) -> dict:
    """Validate a Telegram WebApp ``initData`` query string.

    Verbatim algorithm per the Telegram Mini Apps spec: pop ``hash``, build the
    sorted ``key=value`` check-string, HMAC-SHA256 it with a secret key derived
    from the bot token (keyed by the literal string ``WebAppData``), and compare
    in constant time. Also rejects stale payloads via ``auth_date``.

    Returns the *raw* parsed initData dict (not just the ``user`` field) so
    callers can also read fields like ``start_param``.
    """
    data = dict(parse_qsl(init_data))
    received_hash = data.pop("hash", "")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(401, "bad initData signature")
    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "bad initData auth_date") from exc
    if time.time() - auth_date > max_age:
        raise HTTPException(401, "initData expired")
    return data


def _parse_referrer_tid(start_param: str | None) -> int | None:
    """Parse a ``ref_<telegram_id>`` start_param into an int, or None if absent/malformed."""
    if not start_param or not start_param.startswith("ref_"):
        return None
    try:
        return int(start_param.removeprefix("ref_"))
    except ValueError:
        return None


async def current_user(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> asyncpg.Record:
    """FastAPI dependency: validate initData, auto-create the user, return their row.

    On first-ever sight of a telegram_id (``get_or_create_user`` returns
    ``was_created=True``): grants ``WELCOME_CREDITS`` and, if the initData
    carries a ``start_param`` of the form ``ref_<telegram_id>``, registers the
    referral. Both are gated strictly on the race-safe ``was_created`` flag
    from ``packages.db.users.get_or_create_user`` — never re-derived from ledger
    state — so a single conditional ``add_entry`` call here cannot double-grant.
    """
    if not x_telegram_init_data:
        raise HTTPException(401, "missing X-Telegram-Init-Data header")

    if not TELEGRAM_TOKEN:
        raise HTTPException(500, "TELEGRAM_TOKEN is not configured")

    data = validate_init_data(x_telegram_init_data, TELEGRAM_TOKEN)

    try:
        tg_user = json.loads(data["user"])
        telegram_id = tg_user["id"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(401, "initData missing/invalid user field") from exc

    username = tg_user.get("username")
    first_name = tg_user.get("first_name")

    pool: asyncpg.Pool = request.app.state.pool

    # User creation + welcome credit grant + referral registration must commit
    # (or roll back) together: if the process crashed between a standalone
    # user-insert commit and a separate credit-insert commit, the one-shot
    # was_created flag would never be True again and the welcome credits
    # would be lost forever. Running get_or_create_user's insert on the same
    # connection/transaction as the follow-up writes closes that window.
    async with pool.acquire() as conn:
        async with conn.transaction():
            user, was_created = await get_or_create_user(conn, telegram_id, username, first_name)

            if was_created:
                await ledger.add_entry(
                    conn, user_id=user["id"], delta=WELCOME_CREDITS, reason="bonus"
                )

                referrer_tid = _parse_referrer_tid(data.get("start_param"))
                if referrer_tid is not None:
                    await referrals.register(
                        conn, referrer_tid=referrer_tid, referred_user_id=user["id"]
                    )

    return user

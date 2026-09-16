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

from packages.db.users import get_or_create_user
from packages.shared.config import TELEGRAM_TOKEN


def validate_init_data(init_data: str, bot_token: str, max_age: int = 3600) -> dict:
    """Validate a Telegram WebApp ``initData`` query string.

    Verbatim algorithm per the Telegram Mini Apps spec: pop ``hash``, build the
    sorted ``key=value`` check-string, HMAC-SHA256 it with a secret key derived
    from the bot token (keyed by the literal string ``WebAppData``), and compare
    in constant time. Also rejects stale payloads via ``auth_date``.

    Returns the *raw* parsed initData dict (not just the ``user`` field).
    """
    data = dict(parse_qsl(init_data))
    received_hash = data.pop("hash", "")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        import logging

        logging.getLogger(__name__).warning(
            "initData signature mismatch: raw_len=%d keys=%s expected=%s received=%s check_string=%r",
            len(init_data), sorted(data.keys()), expected, received_hash, check_string,
        )
        raise HTTPException(401, "bad initData signature")
    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "bad initData auth_date") from exc
    if time.time() - auth_date > max_age:
        raise HTTPException(401, "initData expired")
    return data


async def current_user(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> asyncpg.Record:
    """FastAPI dependency: validate initData, auto-create the user, return their row."""
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

    user, _ = await get_or_create_user(pool, telegram_id, username, first_name)
    return user

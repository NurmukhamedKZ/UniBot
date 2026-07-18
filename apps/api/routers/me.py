"""Profile + UniX credentials + referral endpoints.

Per docs/plan.md Task 10. Sits behind ``current_user`` like every other
router except the Tribute webhook in ``payments.py``.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from apps.api.auth import current_user
from packages.db import ledger, referrals, users
from packages.shared.config import BOT_USERNAME
from packages.shared.crypto import encrypt_password

router = APIRouter()

# How many recent ledger entries / completed tasks GET /me returns.
_HISTORY_LIMIT = 20


def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


async def _recent_ledger_entries(pool: asyncpg.Pool, user_id, limit: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, delta, reason, payment_id, task_id, created_at
        FROM credit_ledger
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    out = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["payment_id"] = str(d["payment_id"]) if d["payment_id"] else None
        d["task_id"] = str(d["task_id"]) if d["task_id"] else None
        out.append(d)
    return out


async def _completed_watch_tasks(pool: asyncpg.Pool, user_id, limit: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, type, lesson_id, course_id, status, finished_at, created_at
        FROM watch_tasks
        WHERE user_id = $1 AND status = 'done'
        ORDER BY finished_at DESC NULLS LAST
        LIMIT $2
        """,
        user_id,
        limit,
    )
    out = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        out.append(d)
    return out


@router.get("/me")
async def get_me(request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    balance = await ledger.get_balance(pool, user["id"])
    history = await _recent_ledger_entries(pool, user["id"], _HISTORY_LIMIT)
    watched = await _completed_watch_tasks(pool, user["id"], _HISTORY_LIMIT)
    return {
        "balance": balance,
        "unix_email_set": user["unix_email"] is not None,
        "history": history,
        "watched": watched,
    }


@router.put("/me/unix-credentials")
async def put_unix_credentials(request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    body = await request.json()
    email = body.get("email")
    password = body.get("password")
    if not email or not password:
        raise HTTPException(422, "body must be {email: str, password: str}")

    encrypted = encrypt_password(password)
    await users.set_unix_credentials(pool, user["id"], email, encrypted)
    return {"unix_email_set": True}


@router.delete("/me/unix-credentials")
async def delete_unix_credentials(request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    # set_unix_credentials has no dedicated "clear" signature; reuse it with
    # NULLs on both columns, which is exactly what the DELETE semantics need.
    await users.set_unix_credentials(pool, user["id"], None, None)
    return {"unix_email_set": False}


@router.get("/referral")
async def get_referral(request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    telegram_id = user["telegram_id"]

    invited, credited = await referrals.get_stats(pool, user["id"])

    link = f"https://t.me/{BOT_USERNAME}?startapp=ref_{telegram_id}"
    return {"link": link, "invited": invited, "credited": credited}

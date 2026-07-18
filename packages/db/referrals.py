"""referrals: registration + one-time referrer bonus on referred user's first watch."""

from __future__ import annotations

import asyncpg

from packages.db import ledger
from packages.shared.config import REFERRAL_CREDITS


async def register(pool: asyncpg.Pool, referrer_tid: int, referred_user_id) -> None:
    """Record that ``referred_user_id`` was invited by the user with telegram_id ``referrer_tid``.

    No-ops if the referrer telegram_id is unknown, if it refers to the same
    person (self-referral), or if this referred user already has a referral
    row (``referred_id`` is UNIQUE — first referrer wins).
    """
    referrer_id = await pool.fetchval(
        "SELECT id FROM users WHERE telegram_id = $1", referrer_tid
    )
    if referrer_id is None or referrer_id == referred_user_id:
        return

    await pool.execute(
        """
        INSERT INTO referrals (referrer_id, referred_id)
        VALUES ($1, $2)
        ON CONFLICT (referred_id) DO NOTHING
        """,
        referrer_id,
        referred_user_id,
    )


async def credit_if_first_watch(pool: asyncpg.Pool, user_id):
    """Credit the referrer once ``user_id`` has completed their first watch.

    Meant to be called right after the worker debits a lesson's credit
    (``credit_ledger`` reason='watch'). Determines "first" by counting
    reason='watch' ledger rows for this user — if exactly one exists (the
    one just inserted by the caller) and there's an uncredited referral row
    for this user, marks it credited and grants the referrer bonus credits,
    all in one transaction. Returns the referrer's user_id if a bonus was
    granted, else None (no referral, already credited, or not their first watch).
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            watch_count = await conn.fetchval(
                "SELECT COUNT(*) FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'",
                user_id,
            )
            if watch_count != 1:
                return None

            referral = await conn.fetchrow(
                """
                SELECT * FROM referrals
                WHERE referred_id = $1 AND credited = false
                FOR UPDATE
                """,
                user_id,
            )
            if referral is None:
                return None

            await conn.execute(
                "UPDATE referrals SET credited = true WHERE referred_id = $1",
                user_id,
            )
            referrer_id = referral["referrer_id"]
            await ledger.add_entry(
                conn,
                referrer_id,
                delta=REFERRAL_CREDITS,
                reason="referral",
            )
            return referrer_id

"""payments table + idempotent crediting."""

from __future__ import annotations

import json

import asyncpg

from packages.db import ledger


async def record_payment_and_credit(
    pool: asyncpg.Pool,
    user_id,
    provider: str,
    external_id: str,
    package: str,
    credits: int,
    amount,
    currency: str,
    raw,
) -> bool:
    """Insert a payment row and credit the user, idempotently.

    ``payments.external_id`` is UNIQUE, so a duplicate webhook/callback with
    the same ``external_id`` hits ``ON CONFLICT (external_id) DO NOTHING``
    and the whole thing becomes a no-op — the ledger entry is only added
    when the payment row was actually (newly) inserted, and both writes
    happen in one transaction so partial credit is impossible.

    Returns True if this call credited the user, False if it was a duplicate.
    """
    # asyncpg has no implicit dict->jsonb codec; encode explicitly and cast.
    raw_json = json.dumps(raw) if not isinstance(raw, str) else raw

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO payments
                    (user_id, provider, external_id, package, credits, amount, currency, raw)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (external_id) DO NOTHING
                RETURNING id
                """,
                user_id,
                provider,
                external_id,
                package,
                credits,
                amount,
                currency,
                raw_json,
            )
            if row is None:
                return False

            await ledger.add_entry(
                conn,
                user_id,
                delta=credits,
                reason="purchase",
                payment_id=row["id"],
            )
            return True

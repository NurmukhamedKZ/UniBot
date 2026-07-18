"""credit_ledger queries. Balance is always derived, never stored."""

from __future__ import annotations

import asyncpg


async def get_balance(pool: asyncpg.Pool, user_id) -> int:
    value = await pool.fetchval(
        "SELECT COALESCE(SUM(delta), 0) FROM credit_ledger WHERE user_id = $1",
        user_id,
    )
    return int(value)


async def add_entry(
    pool_or_conn,
    user_id,
    delta: int,
    reason: str,
    payment_id=None,
    task_id=None,
) -> None:
    """Insert one ledger row.

    ``pool_or_conn`` accepts either an ``asyncpg.Pool`` or an
    ``asyncpg.Connection`` (both expose ``.execute``), so callers already
    inside a transaction (e.g. ``payments.record_payment_and_credit``, or the
    worker crediting a watch alongside ``tasks.finish``) can pass their
    connection to keep everything atomic.
    """
    await pool_or_conn.execute(
        """
        INSERT INTO credit_ledger (user_id, delta, reason, payment_id, task_id)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id,
        delta,
        reason,
        payment_id,
        task_id,
    )

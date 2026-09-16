"""credit_ledger queries. Balance is always derived, never stored."""

from __future__ import annotations

import asyncpg


async def get_balance(pool: asyncpg.Pool, user_id) -> int:
    value = await pool.fetchval(
        "SELECT COALESCE(SUM(delta), 0) FROM credit_ledger WHERE user_id = $1",
        user_id,
    )
    return int(value)


async def get_recent_entries(pool: asyncpg.Pool, user_id, limit: int) -> list[dict]:
    """Return the user's most recent ledger entries, newest first.

    Used by ``GET /me`` for the account history list. ``payment_id``/
    ``task_id`` are stringified (they're UUIDs) so the result is directly
    JSON-serializable.
    """
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

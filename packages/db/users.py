"""users table queries."""

from __future__ import annotations

import asyncpg


async def get_or_create_user(
    pool: asyncpg.Pool,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> tuple[asyncpg.Record, bool]:
    """Return ``(user_row, was_created)`` for ``telegram_id``, creating it if absent.

    Signature: returns a 2-tuple, NOT a bare Record — ``(Record, bool)`` where
    the bool is True iff this call is the one that created the row.

    Tries the insert FIRST (``INSERT ... ON CONFLICT (telegram_id) DO NOTHING
    RETURNING *``). If a row comes back, this call won the race and just
    created the user -> ``(row, True)``. If no row comes back (conflict, user
    already existed), falls back to a plain ``SELECT`` -> ``(row, False)``.

    This ordering (insert-first, not select-then-insert) is what makes the
    "newly created" signal race-safe: only one of two concurrent calls for
    the same brand-new ``telegram_id`` can win the ``RETURNING *``, so only
    one caller ever sees ``True``. Callers that grant one-time welcome
    credits on creation (Task 7) MUST gate on this ``True`` flag rather than
    re-deriving "new" from an empty ledger, which the old select-first
    implementation punted to callers and which was race-prone under
    concurrent initData requests for the same new user.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO users (telegram_id, username, first_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (telegram_id) DO NOTHING
        RETURNING *
        """,
        telegram_id,
        username,
        first_name,
    )
    if row is not None:
        return row, True

    row = await pool.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
    return row, False


async def set_unix_credentials(
    pool: asyncpg.Pool,
    user_id,
    email: str,
    password_enc: str,
) -> None:
    await pool.execute(
        "UPDATE users SET unix_email = $2, unix_password_enc = $3 WHERE id = $1",
        user_id,
        email,
        password_enc,
    )

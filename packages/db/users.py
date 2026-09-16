"""users table queries."""

from __future__ import annotations

import asyncpg


async def get_or_create_user(
    pool_or_conn,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> tuple[asyncpg.Record, bool]:
    """Return ``(user_row, was_created)`` for ``telegram_id``, creating it if absent.

    Signature: returns a 2-tuple, NOT a bare Record — ``(Record, bool)`` where
    the bool is True iff this call is the one that created the row.

    ``pool_or_conn`` accepts either an ``asyncpg.Pool`` or an ``asyncpg.Connection``.

    Tries the insert FIRST (``INSERT ... ON CONFLICT (telegram_id) DO NOTHING
    RETURNING *``). If a row comes back, this call won the race and just
    created the user -> ``(row, True)``. If no row comes back (conflict, user
    already existed), falls back to a plain ``SELECT`` -> ``(row, False)``.

    """
    row = await pool_or_conn.fetchrow(
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

    row = await pool_or_conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
    return row, False


async def set_credentials(
    pool: asyncpg.Pool,
    user_id,
    email: str | None,
    password_enc: str | None,
) -> None:
    """Set (or, passing ``None``/``None``, clear) a user's KBTU credentials."""
    await pool.execute(
        "UPDATE users SET kbtu_email = $2, kbtu_password_enc = $3 WHERE id = $1",
        user_id,
        email,
        password_enc,
    )

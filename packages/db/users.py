"""users table queries."""

from __future__ import annotations

import asyncpg


async def get_or_create_user(
    pool: asyncpg.Pool,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> asyncpg.Record:
    """Return the existing user row for ``telegram_id``, creating it if absent.

    Looks the user up first so existing rows are returned untouched (no
    clobbering of a possibly-edited username), then falls back to an
    ``INSERT ... ON CONFLICT DO NOTHING`` + re-select, which also handles a
    concurrent create racing us. Callers that need to know whether this was
    a brand-new user (e.g. to grant welcome credits) should check for that
    themselves, e.g. by comparing ``created_at`` or checking the balance.
    """
    row = await pool.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
    if row is not None:
        return row

    await pool.execute(
        """
        INSERT INTO users (telegram_id, username, first_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (telegram_id) DO NOTHING
        """,
        telegram_id,
        username,
        first_name,
    )
    row = await pool.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
    return row


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

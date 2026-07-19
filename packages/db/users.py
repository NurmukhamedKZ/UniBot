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

    ``pool_or_conn`` accepts either an ``asyncpg.Pool`` or an
    ``asyncpg.Connection`` (both expose ``.fetchrow``), same pattern as
    ``ledger.add_entry``, so callers that need the user-creation insert to
    commit atomically with follow-up writes (e.g. Task 7's welcome-credit
    grant + referral registration in ``apps.api.auth.current_user``) can pass
    a connection already inside their own transaction.

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


async def set_unix_credentials(
    pool: asyncpg.Pool,
    user_id,
    email: str | None,
    password_enc: str | None,
) -> None:
    """Set (or, passing ``None``/``None``, clear) a user's UniX credentials.

    No separate "clear credentials" function exists in this module -- the
    ``DELETE /me/unix-credentials`` endpoint reuses this with both arguments
    ``None``, which is exactly the NULL/NULL update it needs.
    """
    await pool.execute(
        "UPDATE users SET unix_email = $2, unix_password_enc = $3 WHERE id = $1",
        user_id,
        email,
        password_enc,
    )


async def get_id_by_telegram_id(pool: asyncpg.Pool, telegram_id: int):
    """Look up a user's internal ``id`` by their Telegram id, or ``None``.

    Used by the Tribute webhook (``apps.api.routers.payments``), which
    resolves a user from a Telegram id embedded in Tribute's payload rather
    than from a Telegram-initData-authenticated request.
    """
    return await pool.fetchval("SELECT id FROM users WHERE telegram_id = $1", telegram_id)


async def get_by_id(pool: asyncpg.Pool, user_id) -> asyncpg.Record | None:
    """Fetch a full user row by internal ``id``, or ``None`` if it doesn't exist.

    Used by the worker (``apps.worker.executor``) to resolve a task owner's
    ``telegram_id`` (for the Bot API chat_id) and UniX credentials
    (``unix_email``/``unix_password_enc``, the latter still Fernet-encrypted
    -- callers decrypt with ``packages.shared.crypto.decrypt_password``), and
    to resolve a referrer's ``telegram_id`` for the referral-bonus notification.
    """
    return await pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)

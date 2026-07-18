"""asyncpg connection pool factory for UniBot.

All of api/bot/worker share this to create their single process-wide pool.
Migrations are the caller's responsibility (see ``packages.db.migrate``).
"""

from __future__ import annotations

import asyncpg

from packages.shared.config import DATABASE_URL


async def create_pool() -> asyncpg.Pool:
    """Create the process-wide asyncpg pool using ``DATABASE_URL`` from config."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return await asyncpg.create_pool(dsn=DATABASE_URL)

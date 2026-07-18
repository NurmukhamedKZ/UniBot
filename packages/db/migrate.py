"""Simple forward-only SQL migration runner for UniBot.

Runs every ``migrations/*.sql`` file in filename order, skipping any file
already recorded in the ``schema_migrations`` table (tracked by filename).
Each migration is applied inside its own transaction and recorded as
applied immediately afterwards.

Usage as a library (e.g. called on API startup)::

    from packages.db.migrate import run_migrations

    pool = await asyncpg.create_pool(dsn=DATABASE_URL)
    await run_migrations(pool)

Usage as a CLI (creates its own pool from ``DATABASE_URL``)::

    python -m packages.db.migrate
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def _discover_migrations(migrations_dir: Path) -> list[Path]:
    """Return all *.sql files in ``migrations_dir``, sorted by filename."""
    if not migrations_dir.is_dir():
        return []
    return sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)


async def run_migrations(pool: asyncpg.Pool, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every not-yet-applied migration in ``migrations_dir``.

    Migrations are applied in filename order. Already-applied migrations
    (tracked by filename in ``schema_migrations``) are skipped. Returns the
    list of filenames that were newly applied.
    """
    migration_files = _discover_migrations(migrations_dir)
    if not migration_files:
        logger.info("No migration files found in %s", migrations_dir)
        return []

    applied: list[str] = []

    async with pool.acquire() as conn:
        # schema_migrations itself is created by 001_init.sql, so on a
        # brand-new database it won't exist yet — treat that as "nothing
        # applied so far" rather than pre-creating it (which would clash
        # with 001_init.sql's own CREATE TABLE statement).
        table_exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations')")
        if table_exists:
            already_applied = {
                row["filename"]
                for row in await conn.fetch("SELECT filename FROM schema_migrations")
            }
        else:
            already_applied = set()

        for path in migration_files:
            if path.name in already_applied:
                logger.info("Skipping already-applied migration %s", path.name)
                continue

            sql = path.read_text()
            logger.info("Applying migration %s", path.name)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1) "
                    "ON CONFLICT (filename) DO NOTHING",
                    path.name,
                )
            applied.append(path.name)

    return applied


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    pool = await asyncpg.create_pool(dsn=database_url)
    try:
        applied = await run_migrations(pool)
        if applied:
            logger.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
        else:
            logger.info("Database already up to date, no migrations applied")
    finally:
        await pool.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()

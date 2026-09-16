"""Shared fixtures for packages/db tests.

Tests run against a REAL local Postgres (no mocks), matching Task 5's brief.
Point them at it via ``TEST_DATABASE_URL`` (falls back to ``DATABASE_URL``,
then to a sensible local default). Migrations are applied once per session;
each test gets a clean slate via table truncation.
"""

from __future__ import annotations

import os
import uuid

# Must run before any import (direct or transitive) of packages.shared.crypto,
# which raises RuntimeError at import time if ENCRYPTION_KEY isn't set. Tests
# (e.g. tests/test_me.py) import it at module scope, so without this the
# entire suite fails to collect unless ENCRYPTION_KEY is exported manually
# first. setdefault() so a real deployment/CI-provided key still wins.
from cryptography.fernet import Fernet

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

import asyncpg
import pytest
import pytest_asyncio

from packages.db.migrate import run_migrations

TEST_DATABASE_URL = (
    os.environ.get("TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://unibot:unibot@localhost:5433/unibot"
)


@pytest_asyncio.fixture(scope="session")
async def _session_pool():
    pool = await asyncpg.create_pool(dsn=TEST_DATABASE_URL)
    await run_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def pool(_session_pool):
    """Function-scoped pool with a clean set of tables for every test."""
    async with _session_pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE uninet_courses, uninet_weeks, watch_tasks, transcripts, users CASCADE"
        )
    return _session_pool


@pytest_asyncio.fixture
async def make_user(pool):
    """Factory fixture: create a bare user row, return its id."""

    async def _make_user(telegram_id: int | None = None) -> uuid.UUID:
        tid = telegram_id if telegram_id is not None else uuid.uuid4().int % (2**31)
        row = await pool.fetchrow(
            "INSERT INTO users (telegram_id, username, first_name) "
            "VALUES ($1, $2, $3) RETURNING id",
            tid,
            f"user{tid}",
            "Test",
        )
        return row["id"]

    return _make_user

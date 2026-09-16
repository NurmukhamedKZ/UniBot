"""watch_tasks queue (сейчас только транскрибация): enqueue/claim/finish."""

from __future__ import annotations

import asyncpg


async def enqueue(pool: asyncpg.Pool, user_id, type: str, lesson_id: str | None = None):
    return await pool.fetchval(
        """
        INSERT INTO watch_tasks (user_id, type, lesson_id)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        user_id,
        type,
        lesson_id,
    )


async def claim_next(pool: asyncpg.Pool) -> asyncpg.Record | None:
    """Atomically claim the oldest queued task and mark it running.

    ``FOR UPDATE SKIP LOCKED`` lets multiple worker instances poll this
    concurrently without blocking on each other or double-claiming a row.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT * FROM watch_tasks
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            if row is None:
                return None
            await conn.execute(
                "UPDATE watch_tasks SET status='running', started_at=now() WHERE id=$1",
                row["id"],
            )
            # re-fetch so the returned Record reflects the update we just made
            return await conn.fetchrow("SELECT * FROM watch_tasks WHERE id = $1", row["id"])


async def finish(pool_or_conn, task_id, status: str, error: str | None = None) -> None:
    """Mark a task done/failed/cancelled."""
    await pool_or_conn.execute(
        """
        UPDATE watch_tasks
        SET status = $2, error = $3, finished_at = now()
        WHERE id = $1
        """,
        task_id,
        status,
        error,
    )





async def get_task(pool: asyncpg.Pool, task_id) -> asyncpg.Record | None:
    """Fetch a single task by id, or None if it doesn't exist."""
    return await pool.fetchrow("SELECT * FROM watch_tasks WHERE id = $1", task_id)




async def requeue_stale_running(pool: asyncpg.Pool) -> int:
    """Reset any ``running`` task back to ``queued``.

    Called on worker startup: a crash/restart may have left tasks stuck in
    ``running``.
    """
    result = await pool.execute(
        "UPDATE watch_tasks SET status = 'queued', started_at = NULL WHERE status = 'running'"
    )
    # asyncpg execute() returns a string like "UPDATE 3"
    return int(result.split()[-1])


async def latest_transcribe_task(pool: asyncpg.Pool, lesson_id: str) -> asyncpg.Record | None:
    """Последняя задача транскрибации этого видео (для статуса и дедупликации)."""
    return await pool.fetchrow(
        """
        SELECT * FROM watch_tasks
        WHERE type = 'transcribe' AND lesson_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        lesson_id,
    )

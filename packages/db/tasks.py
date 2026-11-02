"""watch_tasks queue: транскрибация (``transcribe``) и автопросмотр недели (``watch``)."""

from __future__ import annotations

import asyncpg


async def enqueue(pool: asyncpg.Pool, user_id, type: str, lesson_id: str | None = None):
    """id новой задачи; None — такая watch-задача уже в очереди (idx_watch_tasks_one_active_watch)."""
    return await pool.fetchval(
        """
        INSERT INTO watch_tasks (user_id, type, lesson_id)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        user_id,
        type,
        lesson_id,
    )


async def claim_next(pool: asyncpg.Pool, type: str | None = None) -> asyncpg.Record | None:
    """Atomically claim the oldest queued task (of ``type``, if given) and mark it running.

    ``FOR UPDATE SKIP LOCKED`` lets multiple worker instances poll this
    concurrently without blocking on each other or double-claiming a row.
    ``watch`` юзера берётся, только пока у него нет другой запущенной: uninet
    засчитывает время просмотра на юзера, параллельные недели ничего не дают.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT * FROM watch_tasks t
                WHERE status = 'queued' AND ($1::text IS NULL OR type = $1)
                  AND NOT (type = 'watch' AND EXISTS (
                      SELECT 1 FROM watch_tasks r
                      WHERE r.user_id = t.user_id AND r.type = 'watch' AND r.status = 'running'))
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                type,
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


async def latest_watch_task(pool: asyncpg.Pool, user_id, week_id: int) -> asyncpg.Record | None:
    """Последний автопросмотр этой недели этим юзером (для статуса в UI)."""
    return await pool.fetchrow(
        """
        SELECT * FROM watch_tasks
        WHERE type = 'watch' AND user_id = $1 AND lesson_id = $2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user_id,
        str(week_id),
    )

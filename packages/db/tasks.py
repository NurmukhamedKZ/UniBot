"""watch_tasks queue: enqueue/claim/finish/progress."""

from __future__ import annotations

import json

import asyncpg


class TaskAlreadyActiveError(Exception):
    """Raised by ``enqueue`` when the user already has an active lesson/course task.

    Backed by the partial unique index ``idx_watch_tasks_one_active_per_user``
    (one active queued/running lesson-or-course task per user; transcribe
    tasks are unlimited). Callers (e.g. ``POST /watch`` in the API) catch
    this and turn it into ``409 {error: "task_already_active"}``.
    """


async def enqueue(
    pool: asyncpg.Pool,
    user_id,
    type: str,
    lesson_id: str | None = None,
    course_id: str | None = None,
):
    try:
        task_id = await pool.fetchval(
            """
            INSERT INTO watch_tasks (user_id, type, lesson_id, course_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            user_id,
            type,
            lesson_id,
            course_id,
        )
    except asyncpg.UniqueViolationError as exc:
        raise TaskAlreadyActiveError(
            f"user {user_id} already has an active task"
        ) from exc
    return task_id


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
    """Mark a task done/failed/cancelled.

    ``pool_or_conn`` accepts either an ``asyncpg.Pool`` or an
    ``asyncpg.Connection`` (both expose ``.execute``), following the same
    pattern as ``ledger.add_entry``. This lets the worker join this update
    into the SAME transaction as the credit debit (``ledger.add_entry`` with
    reason='watch') by passing its acquired connection, so a crash between
    the two can never leave one committed without the other (which would
    otherwise strand the task ``running`` for ``requeue_stale_running`` to
    re-queue and re-charge on re-watch).
    """
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


async def update_progress(pool_or_conn, task_id, progress: dict) -> None:
    """Update a task's progress JSON.

    ``pool_or_conn`` accepts either an ``asyncpg.Pool`` or an
    ``asyncpg.Connection``, same pattern as ``finish``/``ledger.add_entry``.
    """
    await pool_or_conn.execute(
        "UPDATE watch_tasks SET progress = $2::jsonb WHERE id = $1",
        task_id,
        json.dumps(progress),
    )


async def requeue_stale_running(pool: asyncpg.Pool) -> int:
    """Reset any ``running`` task back to ``queued``.

    Called on worker startup: a crash/restart may have left tasks stuck in
    ``running``. No credits are lost either way since a lesson's credit is
    only debited on successful completion.
    """
    result = await pool.execute(
        "UPDATE watch_tasks SET status = 'queued', started_at = NULL WHERE status = 'running'"
    )
    # asyncpg execute() returns a string like "UPDATE 3"
    return int(result.split()[-1])

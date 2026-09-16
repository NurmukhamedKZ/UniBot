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
    """Shallow-merge ``progress`` into a task's existing ``progress`` jsonb.

    ``pool_or_conn`` accepts either an ``asyncpg.Pool`` or an
    ``asyncpg.Connection``, same pattern as ``finish``/``ledger.add_entry``.

    Uses Postgres's atomic ``jsonb || jsonb`` merge (server-side, single
    statement) rather than a full-column replace: keys in ``progress``
    overwrite matching keys already stored, but unrelated keys survive. This
    matters because the worker (owner of ``current``/``total``/``scores``)
    and the cancel endpoint (owner of ``cancel_requested``, via
    ``merge_progress``) both write this column concurrently -- a blind
    replace here would silently wipe out a ``cancel_requested`` flag set by
    the API on the worker's next progress write.
    """
    await pool_or_conn.execute(
        "UPDATE watch_tasks SET progress = progress || $2::jsonb WHERE id = $1",
        task_id,
        json.dumps(progress),
    )


async def record_lesson_completion(
    pool_or_conn, user_id, lesson_id: str, course_id: str | None = None
):
    """Insert a completed, non-claimable ``watch_tasks`` marker row for one lesson.

    Used by ``run_course`` (apps/worker/executor.py, Task 12 review fix) so
    each course-watched lesson gets its own ``lesson_id``-bearing
    ``watch_tasks`` row -- the SAME shape ``catalog.get_watched_lesson_ids``'s
    join already expects for a ``run_lesson``-produced watch. A course task
    row itself has ``lesson_id = NULL`` (only ``course_id`` is set, correct
    per schema for a course-type task) -- so without a marker row like this,
    a course-driven watch is architecturally invisible to
    ``get_watched_lesson_ids``, and re-running the SAME course after topping
    up credits would re-watch and re-charge lessons already paid for in a
    prior run (the exact bug this function fixes).

    Inserted directly as ``status='done'`` (never ``'queued'``) so it:
      - is NEVER picked up by ``claim_next`` (only selects ``status='queued'``);
      - is NEVER returned by ``GET /tasks/active`` (only ``status IN
        ('queued','running')``);
      - does NOT trip ``idx_watch_tasks_one_active_per_user`` (that partial
        index only covers rows with ``status IN ('queued','running')``).
    i.e. this is purely a completion record for ``get_watched_lesson_ids`` to
    join against -- not a real queue entry, and it can't collide with the
    course task's own "one active task per user" slot.

    ``pool_or_conn`` accepts either an ``asyncpg.Pool`` or an
    ``asyncpg.Connection`` so the caller can insert this in the SAME
    transaction as the matching ``credit_ledger`` debit (same pattern as
    ``finish``/``ledger.add_entry``).
    """
    return await pool_or_conn.fetchval(
        """
        INSERT INTO watch_tasks (user_id, type, lesson_id, course_id, status, started_at, finished_at)
        VALUES ($1, 'lesson', $2, $3, 'done', now(), now())
        RETURNING id
        """,
        user_id,
        lesson_id,
        course_id,
    )


async def get_active_task(pool: asyncpg.Pool, user_id) -> asyncpg.Record | None:
    """Return the user's active (queued/running) lesson/course task, if any.

    Mirrors the partial unique index ``idx_watch_tasks_one_active_per_user``:
    only ``type IN ('lesson','course')`` counts as "active" for this lookup
    (unlimited ``transcribe`` tasks are deliberately excluded, same as the
    index). Used by ``GET /tasks/active`` for webapp polling.
    """
    return await pool.fetchrow(
        """
        SELECT * FROM watch_tasks
        WHERE user_id = $1 AND status IN ('queued', 'running') AND type IN ('lesson', 'course')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user_id,
    )


async def get_task(pool: asyncpg.Pool, task_id) -> asyncpg.Record | None:
    """Fetch a single task by id, or None if it doesn't exist."""
    return await pool.fetchrow("SELECT * FROM watch_tasks WHERE id = $1", task_id)


async def merge_progress(pool_or_conn, task_id, patch: dict) -> None:
    """Shallow-merge ``patch`` into the task's existing ``progress`` jsonb.

    Same atomic server-side merge as ``update_progress`` (``progress =
    progress || $2::jsonb``, a single statement) so setting e.g.
    ``cancel_requested`` never clobbers fields the worker is concurrently
    maintaining (``current``, ``total``, ``scores``, ...), and is never
    itself lost to a concurrent worker write: there's no read-modify-write
    gap here for a concurrent ``||``-merge UPDATE to race against -- both
    statements are serialized by Postgres's row-level locking and each
    survives.
    """
    await pool_or_conn.execute(
        "UPDATE watch_tasks SET progress = progress || $2::jsonb WHERE id = $1",
        task_id,
        json.dumps(patch),
    )


async def get_completed_tasks(pool: asyncpg.Pool, user_id, limit: int) -> list[dict]:
    """Return the user's most recently finished watch tasks, newest first.

    Used by ``GET /me`` for the "watched" list. ``id`` is stringified (it's
    a UUID) so the result is directly JSON-serializable.
    """
    rows = await pool.fetch(
        """
        SELECT id, type, lesson_id, course_id, status, finished_at, created_at
        FROM watch_tasks
        WHERE user_id = $1 AND status = 'done'
        ORDER BY finished_at DESC NULLS LAST
        LIMIT $2
        """,
        user_id,
        limit,
    )
    out = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        out.append(d)
    return out


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

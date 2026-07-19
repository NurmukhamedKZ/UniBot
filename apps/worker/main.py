"""Worker process entrypoint (Task 11, Phase 3 of docs/plan.md).

On startup: create the shared asyncpg pool + one process-wide ``aiogram.Bot``
instance (used as a bare Bot-API client, no Dispatcher/polling -- Task 14
builds the actual thin bot process), then ``requeue_stale_running`` so a
previous crash/restart doesn't strand tasks in ``'running'`` forever.

Main loop: ``claim_next()`` -> if ``None``, sleep 3s and retry; else dispatch
to ``run_lesson`` bounded by ``asyncio.Semaphore(2)`` (docs/plan.md Global
Constraints: shared VPS, "≤2 параллельных Chromium").

``type='course'`` tasks (Task 12) dispatch to ``run_course`` the same way
``type='lesson'`` dispatches to ``run_lesson`` -- both bounded by the same
semaphore, since both drive a Playwright/Chromium session. ``type='transcribe'``
tasks still go through ``_requeue_unsupported`` below (Task 13's scope).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from apps.worker.executor import run_course, run_lesson
from packages.db import tasks
from packages.db.pool import create_pool
from packages.shared.config import TELEGRAM_TOKEN

logger = logging.getLogger(__name__)

MAX_CONCURRENT_BROWSERS = 2
POLL_INTERVAL_SECONDS = 3

_EXECUTORS = {"lesson": run_lesson, "course": run_course}


async def _requeue_unsupported(pool, task) -> None:
    """Put a claimed task of a type this worker can't execute yet back to 'queued'.

    ``claim_next()`` (packages/db/tasks.py) has no ``type`` filter -- it
    always claims the single oldest ``'queued'`` row and marks it
    ``'running'``, regardless of type. This loop can run ``type='lesson'``
    (Task 11) and ``type='course'`` (Task 12) tasks; a claimed
    ``'transcribe'`` row still has to be put back rather than left stranded
    ``'running'`` (which ``requeue_stale_running`` would only recover after a
    full worker restart) until Task 13 lands ``run_transcribe``.

    Bumps ``created_at = now()`` (and clears ``started_at``, matching
    ``requeue_stale_running``'s convention in packages/db/tasks.py, since
    the task never actually started running) when requeuing. Without this,
    a requeued row keeps its original ``created_at`` and -- since
    ``claim_next()`` orders ``BY created_at`` -- stays at the FRONT of the
    FIFO queue, so it gets immediately re-claimed on every poll and
    permanently starves any ``'lesson'`` task queued behind it. Bumping
    ``created_at`` rotates it to the back of the queue instead, so other
    runnable tasks ahead of it get a turn. This is a Task-11-local stopgap
    for the still-unhandled types; Task 13 should land the real
    ``run_transcribe`` executor and drop this function (a ``type`` filter on
    ``claim_next()`` itself is deliberately NOT added here -- that's shared
    ground-truth code other tasks may need unfiltered).
    """
    await pool.execute(
        "UPDATE watch_tasks SET status = 'queued', created_at = now(), started_at = NULL WHERE id = $1",
        task["id"],
    )
    logger.info(
        "task %s (type=%s) requeued to back of queue -- no executor for this type yet",
        task["id"],
        task["type"],
    )


async def _dispatch(pool, bot, semaphore: asyncio.Semaphore, task) -> None:
    executor = _EXECUTORS[task["type"]]
    async with semaphore:
        try:
            await executor(pool, bot, task)
        except Exception:
            logger.exception("%s crashed for task=%s", executor.__name__, task["id"])
            try:
                await tasks.finish(pool, task["id"], "failed", error="worker crashed")
            except Exception:
                logger.exception("failed to mark crashed task=%s as failed", task["id"])


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    pool = await create_pool()
    bot = Bot(TELEGRAM_TOKEN)

    requeued = await tasks.requeue_stale_running(pool)
    logger.info("requeued %d stale running task(s) on startup", requeued)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROWSERS)
    background_tasks: set[asyncio.Task] = set()

    try:
        while True:
            task = await tasks.claim_next(pool)
            if task is None:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            if task["type"] not in _EXECUTORS:
                await _requeue_unsupported(pool, task)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            job = asyncio.create_task(_dispatch(pool, bot, semaphore, task))
            background_tasks.add(job)
            job.add_done_callback(background_tasks.discard)
    finally:
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

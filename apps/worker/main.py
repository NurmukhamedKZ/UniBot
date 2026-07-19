"""Worker process entrypoint (Task 11, Phase 3 of docs/plan.md).

On startup: create the shared asyncpg pool + one process-wide ``aiogram.Bot``
instance (used as a bare Bot-API client, no Dispatcher/polling -- Task 14
builds the actual thin bot process), then ``requeue_stale_running`` so a
previous crash/restart doesn't strand tasks in ``'running'`` forever.

Main loop: ``claim_next()`` -> if ``None``, sleep 3s and retry; else dispatch
to ``run_lesson`` bounded by ``asyncio.Semaphore(2)`` (docs/plan.md Global
Constraints: shared VPS, "≤2 параллельных Chromium").

``type='course'`` and ``type='transcribe'`` tasks: see ``_requeue_unsupported``
below for the judgment call on how they're handled -- neither ``run_course``
(Task 12) nor ``run_transcribe``/the catalog scraper (Task 13) exist yet.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from apps.worker.executor import run_lesson
from packages.db import tasks
from packages.db.pool import create_pool
from packages.shared.config import TELEGRAM_TOKEN

logger = logging.getLogger(__name__)

MAX_CONCURRENT_BROWSERS = 2
POLL_INTERVAL_SECONDS = 3


async def _requeue_unsupported(pool, task) -> None:
    """Put a claimed task of a type this worker can't execute yet back to 'queued'.

    ``claim_next()`` (packages/db/tasks.py) has no ``type`` filter -- it
    always claims the single oldest ``'queued'`` row and marks it
    ``'running'``, regardless of type. Since this loop can only run
    ``type='lesson'`` tasks (Task 11's scope), a claimed ``'course'`` or
    ``'transcribe'`` row has to be put back rather than left stranded
    ``'running'`` (which ``requeue_stale_running`` would only recover after a
    full worker restart).

    KNOWN LIMITATION for Task 12/13: because ``claim_next()`` always picks
    the oldest ``'queued'`` row first (FIFO by ``created_at``), an
    unsupported task sitting at the front of the queue will keep winning the
    claim race against any ``'lesson'`` task queued after it -- it gets
    claimed, immediately requeued, and claimed again on the next poll,
    effectively starving lesson tasks behind it until ``run_course``/
    ``run_transcribe`` land or the task is cancelled. Acceptable for this
    task's scope per the Task 11 brief; Task 12/13 should either add a
    ``type`` filter to ``claim_next()`` (it doesn't need one until an
    unsupported type can actually be enqueued and this worker is the only
    consumer -- true today) or land the missing executors promptly.
    """
    await pool.execute("UPDATE watch_tasks SET status = 'queued' WHERE id = $1", task["id"])
    logger.info(
        "task %s (type=%s) requeued -- no executor for this type yet", task["id"], task["type"]
    )


async def _dispatch(pool, bot, semaphore: asyncio.Semaphore, task) -> None:
    async with semaphore:
        try:
            await run_lesson(pool, bot, task)
        except Exception:
            logger.exception("run_lesson crashed for task=%s", task["id"])
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

            if task["type"] != "lesson":
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

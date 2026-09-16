"""Worker: очередь ``watch_tasks`` с ``type='transcribe'``.

``lesson_id`` задачи — id видео Kinescope. Кредов студента не нужно: аудио
берётся с Kinescope напрямую (см. packages/core/uninet.py).
"""

from __future__ import annotations

import asyncio
import logging

from packages.core.transcribe import transcribe
from packages.db import catalog, tasks
from packages.db.pool import create_pool

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 2  # ffmpeg + загрузка в OpenAI; VPS общий
POLL_INTERVAL_SECONDS = 3


async def run_transcribe(pool, task) -> None:
    kid = task["lesson_id"]
    try:
        if await catalog.get_transcript(pool, kid) is None:
            text = await transcribe(kid)
            await catalog.save_transcript(pool, kid, text)
            logger.info("transcribed %s (%d chars)", kid, len(text))
        await tasks.finish(pool, task["id"], "done")
    except Exception as exc:
        logger.exception("transcribe failed for %s", kid)
        await tasks.finish(pool, task["id"], "failed", error=str(exc)[:500])


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    pool = await create_pool()
    logger.info("requeued %d stale running task(s)", await tasks.requeue_stale_running(pool))

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    running: set[asyncio.Task] = set()

    async def guarded(task):
        async with semaphore:
            await run_transcribe(pool, task)

    try:
        while True:
            if semaphore.locked():
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            task = await tasks.claim_next(pool)
            if task is None:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            if task["type"] != "transcribe":
                # lesson/course — задачи старого автопросмотра UniX (legacy/), больше не исполняются.
                await tasks.finish(pool, task["id"], "cancelled", error="legacy task type")
                continue
            job = asyncio.create_task(guarded(task))
            running.add(job)
            job.add_done_callback(running.discard)
    finally:
        await asyncio.gather(*running, return_exceptions=True)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

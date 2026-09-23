"""Worker: очередь ``watch_tasks`` с ``type='transcribe'`` + слежка за wsp.kbtu.kz.

``lesson_id`` задачи — id видео Kinescope. Кредов студента не нужно: аудио
берётся с Kinescope напрямую (см. packages/core/uninet.py).

Слежка: раз в ``ATTENDANCE_INTERVAL_SECONDS`` у каждого юзера с кредами
проверяется «Отметка посещения» на wsp; при переходе закрыто -> открыто бот
шлёт уведомление (см. packages/core/wsp.py).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from packages.core import wsp
from packages.core.transcribe import transcribe
from packages.core.uninet import InvalidCredentials, UninetUnavailable
from packages.db import catalog, tasks, users
from packages.db.pool import create_pool
from packages.shared.config import TELEGRAM_TOKEN
from packages.shared.crypto import decrypt_password

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 2  # ffmpeg + загрузка в OpenAI; VPS общий
POLL_INTERVAL_SECONDS = 3
ATTENDANCE_INTERVAL_SECONDS = 120  # окно отметки на паре короткое
ATTENDANCE_CONCURRENCY = 5


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


async def check_attendance(pool, bot: Bot, user) -> None:
    try:
        page = await wsp.registration_texts(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]))
    except (InvalidCredentials, UninetUnavailable) as exc:
        logger.warning("wsp check failed for user %s: %r", user["id"], exc)
        return
    is_open = wsp.is_open(page)
    if is_open and not user["wsp_open"]:
        await bot.send_message(
            user["telegram_id"],
            f"🔔 Открылась отметка посещения на wsp.kbtu.kz\n\n{wsp.attendance_text(page)}".strip(),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Отметиться", url=wsp.BASE)]]),
        )
        logger.info("attendance open for user %s", user["id"])
    if is_open != user["wsp_open"]:
        await users.set_wsp_open(pool, user["id"], is_open)


async def watch_attendance(pool, bot: Bot) -> None:
    semaphore = asyncio.Semaphore(ATTENDANCE_CONCURRENCY)

    async def guarded(user):
        async with semaphore:
            try:
                await check_attendance(pool, bot, user)
            except Exception:
                logger.exception("attendance check crashed for user %s", user["id"])

    while True:
        try:
            await asyncio.gather(*(guarded(u) for u in await users.with_credentials(pool)))
        except Exception:
            logger.exception("attendance round failed")
        await asyncio.sleep(ATTENDANCE_INTERVAL_SECONDS)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    pool = await create_pool()
    logger.info("requeued %d stale running task(s)", await tasks.requeue_stale_running(pool))

    bot = Bot(TELEGRAM_TOKEN)
    attendance = asyncio.create_task(watch_attendance(pool, bot))

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
        attendance.cancel()
        await asyncio.gather(attendance, *running, return_exceptions=True)
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

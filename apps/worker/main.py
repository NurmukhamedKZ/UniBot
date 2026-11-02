"""Worker: очередь ``watch_tasks`` + слежка за wsp.kbtu.kz.

``transcribe``: ``lesson_id`` — id видео Kinescope. Кредов студента не нужно:
аудио берётся с Kinescope напрямую (см. packages/core/uninet.py).

``watch``: ``lesson_id`` — id недели uninet; воркер «смотрит» её видео тиками
прогресса в реальном времени от имени юзера (uninet засчитывает только реальное
время, docs/uninet.md). Недели одного юзера — по очереди, разных — параллельно.

Слежка: раз в ``ATTENDANCE_INTERVAL_SECONDS`` у каждого юзера с кредами
проверяется «Отметка посещения» на wsp; при переходе закрыто -> открыто бот
шлёт уведомление (см. packages/core/wsp.py).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from packages.core import uninet, wsp
from packages.core.transcribe import transcribe
from packages.core.uninet import InvalidCredentials, UninetUnavailable
from packages.db import catalog, tasks, users
from packages.db.pool import create_pool
from packages.shared.config import TELEGRAM_TOKEN
from packages.shared.crypto import decrypt_password

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 2  # ffmpeg + загрузка в OpenAI; на VM 1 GB RAM
WATCH_CONCURRENCY = 50  # просмотр — это POST раз в 30 с, держать можно много
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


async def run_watch(pool, task) -> None:
    user = await users.credentials_by_id(pool, task["user_id"])
    try:
        if user is None:
            raise InvalidCredentials()
        s = uninet.session(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]))
        await s.watch_week(int(task["lesson_id"]))
        await tasks.finish(pool, task["id"], "done")
        logger.info("watched week %s for user %s", task["lesson_id"], task["user_id"])
    except Exception as exc:
        logger.exception("watch failed for week %s", task["lesson_id"])
        await tasks.finish(pool, task["id"], "failed", error=repr(exc)[:500])


async def consume(pool, type: str, run, concurrency: int) -> None:
    """Забирать задачи ``type`` из очереди, держа не больше ``concurrency`` одновременно."""
    semaphore = asyncio.Semaphore(concurrency)
    running: set[asyncio.Task] = set()

    async def guarded(task):
        try:
            await run(pool, task)
        finally:
            semaphore.release()

    try:
        while True:
            await semaphore.acquire()
            task = await tasks.claim_next(pool, type)
            if task is None:
                semaphore.release()
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            job = asyncio.create_task(guarded(task))
            running.add(job)
            job.add_done_callback(running.discard)
    finally:
        for job in running:
            job.cancel()
        await asyncio.gather(*running, return_exceptions=True)


def attendance_keyboard(page: dict) -> InlineKeyboardMarkup:
    """По кнопке «Отметиться» на каждую открытую пару (жмёт бот, см. apps/bot) + ссылка на сайт."""
    rows = [[InlineKeyboardButton(text=f"✅ Отметиться: {lesson.title}", callback_data=wsp.MARK_PREFIX + lesson.key)]
            for lesson in wsp.lessons(page)]
    rows.append([InlineKeyboardButton(text="Открыть wsp.kbtu.kz", url=wsp.BASE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def check_attendance(pool, bot: Bot, user) -> None:
    try:
        page = await wsp.open_page(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]))
    except (InvalidCredentials, UninetUnavailable) as exc:
        logger.warning("wsp check failed for user %s: %r", user["id"], exc)
        return
    page_texts = wsp.texts(page)
    is_open = wsp.is_open(page_texts)
    if is_open and not user["wsp_open"]:
        await bot.send_message(
            user["telegram_id"],
            f"🔔 Открылась отметка посещения на wsp.kbtu.kz\n\n{wsp.attendance_text(page_texts)}".strip(),
            reply_markup=attendance_keyboard(page),
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
    try:
        await asyncio.gather(
            watch_attendance(pool, bot),
            consume(pool, "transcribe", run_transcribe, MAX_CONCURRENT),
            consume(pool, "watch", run_watch, WATCH_CONCURRENCY),
        )
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

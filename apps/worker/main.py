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
from aiogram.types import BufferedInputFile

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


async def run_transcribe(pool, bot: Bot, task) -> None:
    kid = task["lesson_id"]
    try:
        text = await catalog.get_transcript(pool, kid)
        if text is None:
            text = await transcribe(kid)
            await catalog.save_transcript(pool, kid, text)
            logger.info("transcribed %s (%d chars)", kid, len(text))
        await tasks.finish(pool, task["id"], "done")
    except Exception as exc:
        logger.exception("transcribe failed for %s", kid)
        await tasks.finish(pool, task["id"], "failed", error=str(exc)[:500])
        # Детали (Kinescope/OpenAI) — в лог, юзеру — общий текст.
        await notify(pool, bot, task["user_id"],
                     lambda: _with_title(pool, kid, "⚠️ Не удалось сделать транскриб{}. Попробуй позже."))
        return
    await notify(pool, bot, task["user_id"], lambda: _with_title(pool, kid, "📝 Транскриб готов{}"),
                 BufferedInputFile(text.encode("utf-8"), filename=f"transcript-{kid[:12]}.txt"))


async def run_watch(pool, bot: Bot, task) -> None:
    week_id = int(task["lesson_id"])
    user = await users.credentials_by_id(pool, task["user_id"])
    try:
        if user is None:
            raise InvalidCredentials()
        s = uninet.session(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]))
        watched = await s.watch_week(week_id)
        await tasks.finish(pool, task["id"], "done")
        logger.info("watched week %s for user %s (watched=%s)", week_id, task["user_id"], watched)
    except Exception as exc:
        logger.exception("watch failed for week %s", week_id)
        await tasks.finish(pool, task["id"], "failed", error=repr(exc)[:500])
        hint = "Обнови вход: /login" if isinstance(exc, InvalidCredentials) else "Попробуй ещё раз: /watch"
        await notify(pool, bot, task["user_id"],
                     lambda: _week_text(pool, week_id, f"⚠️ Не удалось досмотреть неделю {{}}. {hint}"))
        return
    # Уже досмотренную неделю (``/watch all`` ставит все открытые) молча пропускаем.
    if watched:
        await notify(pool, bot, task["user_id"], lambda: _week_text(pool, week_id, "✅ Неделя {} просмотрена."))


async def notify(pool, bot: Bot, user_id, text, document: BufferedInputFile | None = None) -> None:
    """Сообщить юзеру о готовой задаче (её могли поставить и из Mini App).

    ``text`` — корутин-фабрика: подпись тоже читает БД, и её ошибка, как и ошибка
    отправки, не должна ронять обработку задачи — статус к этому моменту уже записан.
    """
    if user_id is None:
        return
    try:
        telegram_id = await users.telegram_id_by_id(pool, user_id)
        if telegram_id is None:
            return
        caption = await text()
        if document is not None:
            await bot.send_document(telegram_id, document, caption=caption)
        else:
            await bot.send_message(telegram_id, caption)
    except Exception:
        logger.warning("notify user %s failed", user_id, exc_info=True)


async def _week_text(pool, week_id: int, template: str) -> str:
    cached = await catalog.cached_week(pool, week_id)
    theme = cached[0].get("theme") if cached else None
    return template.format(f"«{theme}»" if theme else f"#{week_id}")


async def _with_title(pool, kid: str, template: str) -> str:
    title = await catalog.video_title(pool, kid)
    return template.format(f": «{title}»" if title else "")


async def consume(pool, bot: Bot, type: str, run, concurrency: int) -> None:
    """Забирать задачи ``type`` из очереди, держа не больше ``concurrency`` одновременно."""
    semaphore = asyncio.Semaphore(concurrency)
    running: set[asyncio.Task] = set()

    async def guarded(task):
        try:
            await run(pool, bot, task)
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
            reply_markup=wsp.attendance_keyboard(page),
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
            consume(pool, bot, "transcribe", run_transcribe, MAX_CONCURRENT),
            consume(pool, bot, "watch", run_watch, WATCH_CONCURRENCY),
        )
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

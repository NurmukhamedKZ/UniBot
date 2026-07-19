"""Lesson-task executor: the consumer side of the queue ``POST /watch``
(Task 9) produces into.

``run_lesson`` owns one ``watch_tasks`` row of type ``'lesson'``: it builds
its own Playwright browser + login cycle around the boxed, unmodified
``UniXWatcher.watch_unix`` (per docs/plan.md's Global Constraints -- the
Playwright/LLM logic itself is off-limits, only DB/notification wiring
changes), retries once on a transient error, and either debits a credit +
marks the task done, or marks it failed with the credit untouched.

Course batching (``run_course``) is explicitly NOT built here -- that's
Task 12 (docs/plan.md). ``apps/worker/main.py`` documents how it currently
treats claimed course-type tasks in the meantime.
"""

from __future__ import annotations

import logging
import re

from playwright.async_api import async_playwright

from apps.worker.notify import make_callbacks
from packages.core.unix_service import UniXWatcher, UnixSiteUnavailableError
from packages.db import ledger, referrals, tasks, users
from packages.shared.crypto import decrypt_password

logger = logging.getLogger(__name__)

# Chromium launch args, viewport, and ignore_https_errors copied verbatim
# from the legacy ``UniXWatcher.watch_unix_videos`` (packages/core/unix_service.py)
# per the Task 11 brief -- these are already tuned for the shared, memory-
# constrained VPS (docs/plan.md Global Constraints) and are NOT to be
# reinvented here.
_CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-background-networking",
    "--disable-default-apps",
    "--mute-audio",
    "--js-flags=--max-old-space-size=256",
]
_VIEWPORT = {"width": 1280, "height": 720}

# "2 ретрая" per docs/plan.md Task 11 == 2 total attempts (1 retry after the
# first failure), not 2 retries on top of an initial attempt.
MAX_ATTEMPTS = 2

_UNIX_UNAVAILABLE_ERROR = "UniX недоступен"

# Same threshold as the legacy ``watch_unix_videos`` (``score_int >= 3.5``):
# a test result below this is a genuine failed attempt, not a success, even
# though ``watch_unix`` returns a non-None string for it.
MIN_PASSING_SCORE = 3.5

_SCORE_RE = re.compile(r"\s*(\d+(?:\.\d+)?)")


def _parse_score(result: str) -> float | None:
    """Extract the numerator score out of a ``watch_unix`` result string.

    Per docs/plan.md Task 11 the documented format is ``"N/5 баллов"``
    (``_get_test_result`` in packages/core/unix_service.py builds it as
    ``f"{score} {total}"`` from the two spans on the result page, e.g.
    ``"5 5 баллов"``/``"1/5 баллов"`` depending on what the site renders) --
    either way the leading number is the numerator we care about. Returns
    ``None`` when the string doesn't start with a number at all (e.g. the
    non-test result ``"video doesn't have test"``), in which case there is
    no score to gate on and the result is treated as a plain success.
    """
    match = _SCORE_RE.match(result)
    if match is None:
        return None
    return float(match.group(1))


async def _notify(bot, chat_id: int, text: str) -> None:
    """Best-effort Bot API sendMessage -- a notification failure must never
    take down task bookkeeping that already happened (credit debit / task
    finish), so this swallows and logs rather than propagating."""
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        logger.exception("failed to notify chat_id=%s", chat_id)


async def _watch_once(watcher: UniXWatcher, email: str, password: str, lesson_id: str) -> str | None:
    """One browser+login+watch_unix cycle. Raises on any failure (including
    login failure, which is folded into a plain exception so the caller's
    retry loop treats it the same as any other transient Playwright hiccup)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
        try:
            context = await browser.new_context(
                ignore_https_errors=True, viewport=_VIEWPORT
            )
            page = await context.new_page()
            if not await watcher._login(page, email, password):
                raise RuntimeError("UniX login failed")
            return await watcher.watch_unix(page, lesson_id)
        finally:
            await browser.close()


async def run_lesson(pool, bot, task) -> None:
    """Execute a single ``watch_tasks`` row of type ``'lesson'`` end-to-end.

    ``task`` is the ``asyncpg.Record`` returned by ``tasks.claim_next`` --
    already marked ``'running'``. On success (``watch_unix`` returns
    non-``None``): debits 1 credit + marks the task ``'done'`` in ONE
    transaction (so a crash between the two can never strand one without
    the other), then -- outside that transaction, since it's a separate
    concern -- credits the referrer on the user's first watch and notifies
    them. On failure after retries are exhausted (or immediately, for the
    distinct ``UnixSiteUnavailableError`` path): marks the task ``'failed'``
    with an error message; the credit is NOT touched. Either way, the task
    owner gets a final Bot API notification.

    NOTE on what counts as "success": a plain non-``None`` result is NOT
    enough. ``watch_unix`` can return a string like ``"Error: ..."`` on some
    internal failures (a parse exception inside ``_do_test``/
    ``_get_test_result``), and it can also return a genuinely low test score
    (e.g. ``"1/5 баллов"``) -- neither is a real success. Matching the
    legacy ``watch_unix_videos`` classification (``"Error" in result`` =>
    failure; ``score_int >= 3.5`` required for success), this executor:
      - treats ``None``/an ``"Error" in result`` string the same as a raised
        exception -- it consumes a retry attempt (``MAX_ATTEMPTS`` total);
      - treats a parseable score below ``MIN_PASSING_SCORE`` as a terminal
        failure -- NOT retried (retrying won't change the LLM's answers),
        the task is marked ``failed`` with a clear "test not passed"
        message and the credit is left untouched;
      - anything else (a passing score, or a result with no score at all
        like ``"video doesn't have test"``) is a genuine success.
    """
    task_id = task["id"]
    user_id = task["user_id"]
    lesson_id = task["lesson_id"]

    user = await users.get_by_id(pool, user_id)
    if user is None or user["unix_email"] is None or user["unix_password_enc"] is None:
        # Credentials were valid when POST /watch enqueued this (Task 9
        # checks them), but the user could have deleted them while queued.
        await tasks.finish(pool, task_id, "failed", error="no unix credentials")
        if user is not None:
            await _notify(
                bot,
                user["telegram_id"],
                f"❌ Не удалось пройти урок #{lesson_id}: креды UniX не заданы. Кредит не списан.",
            )
        return

    chat_id = user["telegram_id"]
    email = user["unix_email"]
    password = decrypt_password(user["unix_password_enc"])

    log_callback, edit_callback, reset_video_callback = make_callbacks(bot, chat_id)
    watcher = UniXWatcher(
        email,
        password,
        pool,
        chat_id,
        log_callback,
        edit_callback,
        reset_video_callback,
    )

    result: str | None = None
    last_error: Exception | None = None
    site_unavailable = False
    low_score_result: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw_result = await _watch_once(watcher, email, password, lesson_id)
        except UnixSiteUnavailableError as exc:
            # Distinct failure path: no retry, no conflation with a generic
            # "failed after N attempts" message (per docs/plan.md Task 11).
            site_unavailable = True
            last_error = exc
            break
        except Exception as exc:
            last_error = exc
            logger.warning(
                "run_lesson attempt %d/%d failed for task=%s lesson=%s: %s",
                attempt,
                MAX_ATTEMPTS,
                task_id,
                lesson_id,
                exc,
            )
            result = None
            continue

        if raw_result is None or "Error" in raw_result:
            # Non-fatal failure string from watch_unix (parse exception
            # inside _do_test/_get_test_result, or a None return) -- treat
            # exactly like a raised exception: consumes a retry attempt.
            last_error = RuntimeError(raw_result if raw_result else "watch_unix returned no result")
            logger.warning(
                "run_lesson attempt %d/%d got a failure result for task=%s lesson=%s: %s",
                attempt,
                MAX_ATTEMPTS,
                task_id,
                lesson_id,
                raw_result,
            )
            result = None
            continue

        score = _parse_score(raw_result)
        if score is not None and score < MIN_PASSING_SCORE:
            # A genuine low score is a terminal failure for this lesson --
            # retrying won't change the LLM's answers, so don't loop on it.
            low_score_result = raw_result
            result = None
            break

        result = raw_result
        last_error = None
        break

    if result is not None:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await ledger.add_entry(conn, user_id, delta=-1, reason="watch", task_id=task_id)
                await tasks.finish(conn, task_id, "done")

        await _notify(bot, chat_id, f"✅ Урок #{lesson_id} завершён — {result}")

        referrer_id = await referrals.credit_if_first_watch(pool, user_id)
        if referrer_id is not None:
            referrer = await users.get_by_id(pool, referrer_id)
            if referrer is not None:
                await _notify(
                    bot,
                    referrer["telegram_id"],
                    "🎁 Ваш друг посмотрел первый урок — вам начислено 3 кредита!",
                )
        return

    if low_score_result is not None:
        error_message = f"не сдан тест: {low_score_result}"
        user_message = (
            f"❌ Урок #{lesson_id}: тест не сдан ({low_score_result}). Кредит не списан."
        )
    elif site_unavailable:
        error_message = _UNIX_UNAVAILABLE_ERROR
        user_message = (
            "⚠️ Сайт UniX сейчас недоступен. Попробуй позже — кредит не списан."
        )
    else:
        error_message = f"failed after {MAX_ATTEMPTS} attempts: {last_error}"
        user_message = f"❌ Не удалось пройти урок #{lesson_id}. Кредит не списан."

    await tasks.finish(pool, task_id, "failed", error=error_message)
    await _notify(bot, chat_id, user_message)

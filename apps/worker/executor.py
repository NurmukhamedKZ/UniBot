"""Lesson-task executor: the consumer side of the queue ``POST /watch``
(Task 9) produces into.

``run_lesson`` owns one ``watch_tasks`` row of type ``'lesson'``: it builds
its own Playwright browser + login cycle around the boxed, unmodified
``UniXWatcher.watch_unix`` (per docs/plan.md's Global Constraints -- the
Playwright/LLM logic itself is off-limits, only DB/notification wiring
changes), retries once on a transient error, and either debits a credit +
marks the task done, or marks it failed with the credit untouched.

``run_course`` (Task 12) owns one ``watch_tasks`` row of type ``'course'``:
it walks the course's not-yet-watched lessons in ``position`` order, running
the SAME per-lesson attempt/retry/classification as ``run_lesson`` via the
shared ``_attempt_lesson`` helper -- but wraps each lesson's outcome in its
own orchestration (skip already-watched, stop on zero balance, stop on
cancel, mark a failed lesson ``skipped`` and keep going rather than failing
the whole course).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from playwright.async_api import async_playwright

from apps.worker.notify import make_callbacks
from packages.core.unix_service import UniXWatcher, UnixSiteUnavailableError
from packages.db import catalog, ledger, referrals, tasks, users
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


@dataclass
class LessonAttempt:
    """Outcome of one ``_attempt_lesson`` call -- the classification result
    shared by ``run_lesson`` and ``run_course`` (Task 12), so both stay on
    the exact same success/retry/terminal-failure rules instead of each
    reimplementing (and potentially drifting from) ``watch_unix``'s output
    classification.

    ``success=True`` => ``result`` is the passing ``watch_unix`` string,
    credit the lesson. ``success=False`` => ``error_message`` is the
    machine-readable reason (for ``tasks.finish``/progress bookkeeping) and
    ``user_message`` is the ready-to-send Telegram text explaining it (used
    as-is by ``run_lesson``; ``run_course`` does not send it per-lesson to
    avoid spamming N failure messages during a batch, but folds
    ``error_message`` into the per-lesson progress entry instead).
    """

    success: bool
    result: str | None = None
    error_message: str | None = None
    user_message: str | None = None


async def _attempt_lesson(
    watcher: UniXWatcher, email: str, password: str, lesson_id: str, task_id
) -> LessonAttempt:
    """Run the retry loop + success/failure classification for ONE lesson.

    Extracted out of ``run_lesson`` (Task 12) so ``run_course`` can drive the
    exact same per-lesson attempt/retry/classification logic -- reusing the
    same ``watcher`` instance across lessons in the course (it holds no
    lesson-specific state; ``_watch_once`` opens a fresh browser+login per
    attempt regardless) rather than each caller reimplementing the retry
    loop. See ``run_lesson``'s docstring for the exact classification rules
    (``"Error" in result`` retried like a raised exception; a parseable score
    below ``MIN_PASSING_SCORE`` is a terminal, unretried failure; anything
    else non-``None`` is success) -- unchanged here, just relocated.
    """
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
                "attempt %d/%d failed for task=%s lesson=%s: %s",
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
                "attempt %d/%d got a failure result for task=%s lesson=%s: %s",
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
        return LessonAttempt(success=True, result=result)

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

    return LessonAttempt(success=False, error_message=error_message, user_message=user_message)


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

    attempt = await _attempt_lesson(watcher, email, password, lesson_id, task_id)

    if attempt.success:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await ledger.add_entry(conn, user_id, delta=-1, reason="watch", task_id=task_id)
                await tasks.finish(conn, task_id, "done")

        await _notify(bot, chat_id, f"✅ Урок #{lesson_id} завершён — {attempt.result}")

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

    await tasks.finish(pool, task_id, "failed", error=attempt.error_message)
    await _notify(bot, chat_id, attempt.user_message)


def _load_progress(task) -> dict:
    """``watch_tasks.progress`` comes back from asyncpg as a jsonb string,
    not a dict (no codec registered on the pool, matching the pattern
    already used by ``tests/test_tasks.py``) -- normalize it once here."""
    progress = task["progress"]
    if isinstance(progress, str):
        return json.loads(progress) if progress else {}
    return dict(progress) if progress else {}


async def run_course(pool, bot, task) -> None:
    """Execute one ``watch_tasks`` row of type ``'course'`` end-to-end.

    ``task`` is the ``asyncpg.Record`` returned by ``tasks.claim_next`` --
    already marked ``'running'``. Walks the course's lessons in ``position``
    order (``catalog.get_course_lessons``), skipping any the user has
    already been credited for (``catalog.get_watched_lesson_ids`` -- a
    lesson counts as watched via a ``credit_ledger`` reason='watch' row
    whose ``task_id`` points at a ``watch_tasks`` row with that lesson_id;
    this also makes a re-run of a partially-completed course resumable).

    For each remaining lesson, in order:
      1. Re-read the task's ``progress`` and check ``cancel_requested`` --
         set by ``POST /tasks/{id}/cancel`` (packages/db/tasks.py's
         ``merge_progress``) between polls. If set: ``finish(cancelled)``
         and stop -- no further lessons attempted.
      2. Check the live balance (``ledger.get_balance``). Zero or negative:
         stop the loop, ``finish(done)`` with ``progress.stopped ==
         "no_credits"`` merged in, and a paywall-pointing notification.
         (Per docs/plan.md Task 11's course rule: a mid-course "out of
         credits" stop is a normal ``done`` outcome, not a ``failed`` one --
         the lessons already watched in this run were paid for and kept.)
      3. Run the SAME per-lesson attempt/retry/classification as
         ``run_lesson`` via ``_attempt_lesson`` (one shared ``UniXWatcher``
         instance is reused across all lessons in the course -- it holds no
         lesson-specific state; a fresh browser+login happens per attempt
         inside ``_watch_once`` regardless).
      4. Success -> debit 1 credit (reason='watch', task_id=this course
         task's id -- same shape ``get_watched_lesson_ids`` reads back for
         resumability) and record a referral bonus check, same as
         ``run_lesson``. Failure after retries (including a terminal
         low-score) -> record the lesson as ``"skipped"`` in progress and
         move on to the next lesson WITHOUT failing the course task (per
         docs/plan.md: "урок: 2 ретрая → skipped (в progress), курс
         продолжается"). Either way the credit ledger truth and the
         progress ``scores`` list are updated after every lesson.

    If every remaining lesson is processed without an early stop (no
    credits / cancel), the task is marked ``done``. No per-lesson Telegram
    notification is sent (unlike ``run_lesson``) to avoid spamming N
    messages for an N-lesson course -- outcomes live in ``progress.scores``
    for the webapp to poll (``GET /tasks/active``), and the user gets one
    final summary notification.
    """
    task_id = task["id"]
    user_id = task["user_id"]
    course_id = task["course_id"]

    user = await users.get_by_id(pool, user_id)
    if user is None or user["unix_email"] is None or user["unix_password_enc"] is None:
        await tasks.finish(pool, task_id, "failed", error="no unix credentials")
        if user is not None:
            await _notify(
                bot,
                user["telegram_id"],
                f"❌ Не удалось начать курс #{course_id}: креды UniX не заданы.",
            )
        return

    chat_id = user["telegram_id"]
    email = user["unix_email"]
    password = decrypt_password(user["unix_password_enc"])

    all_lessons = await catalog.get_course_lessons(pool, course_id)
    watched_ids = await catalog.get_watched_lesson_ids(pool, user_id)
    remaining = [l for l in all_lessons if l["lesson_id"] not in watched_ids]
    total = len(remaining)

    await tasks.update_progress(pool, task_id, {"total": total, "current": 0, "scores": []})

    if total == 0:
        await tasks.finish(pool, task_id, "done")
        await _notify(bot, chat_id, f"✅ Курс #{course_id}: все уроки уже пройдены.")
        return

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

    scores: list[dict] = []

    for i, lesson in enumerate(remaining, start=1):
        lesson_id = lesson["lesson_id"]

        current_task = await tasks.get_task(pool, task_id)
        progress = _load_progress(current_task)
        if progress.get("cancel_requested"):
            await tasks.finish(pool, task_id, "cancelled")
            await _notify(
                bot,
                chat_id,
                f"🛑 Курс #{course_id} отменён на {i - 1}/{total} уроках.",
            )
            return

        balance = await ledger.get_balance(pool, user_id)
        if balance <= 0:
            await tasks.update_progress(pool, task_id, {"stopped": "no_credits"})
            await tasks.finish(pool, task_id, "done")
            await _notify(
                bot,
                chat_id,
                f"💳 Кредиты закончились. Курс остановлен на {i - 1}/{total} "
                "уроках. Пополните баланс в приложении, чтобы продолжить "
                "(кнопка «Пополнить» на экране профиля).",
            )
            return

        attempt = await _attempt_lesson(watcher, email, password, lesson_id, task_id)

        if attempt.success:
            await ledger.add_entry(pool, user_id, delta=-1, reason="watch", task_id=task_id)
            scores.append({"lesson_id": lesson_id, "status": "done", "result": attempt.result})

            referrer_id = await referrals.credit_if_first_watch(pool, user_id)
            if referrer_id is not None:
                referrer = await users.get_by_id(pool, referrer_id)
                if referrer is not None:
                    await _notify(
                        bot,
                        referrer["telegram_id"],
                        "🎁 Ваш друг посмотрел первый урок — вам начислено 3 кредита!",
                    )
        else:
            # Lesson-level failure after retries (including a terminal low
            # score) does NOT fail the course -- skip and keep going.
            scores.append(
                {"lesson_id": lesson_id, "status": "skipped", "result": attempt.error_message}
            )

        await tasks.update_progress(pool, task_id, {"current": i, "total": total, "scores": scores})

    await tasks.finish(pool, task_id, "done")
    done_count = sum(1 for s in scores if s["status"] == "done")
    await _notify(
        bot,
        chat_id,
        f"✅ Курс #{course_id} завершён: {done_count}/{total} уроков пройдено.",
    )

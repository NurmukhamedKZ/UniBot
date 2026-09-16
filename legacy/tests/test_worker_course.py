"""Tests for apps/worker/executor.py's run_course().

Real local Postgres for everything DB-facing (task claiming, ledger,
catalog, referrals) -- same fixture pattern as tests/test_worker_executor.py
and tests/conftest.py. UniX is unreachable right now, so the UniXWatcher
class and the Playwright browser layer are faked/monkeypatched entirely;
nothing about the fakes stands in for the DB.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.worker import executor
from packages.db import catalog, ledger, tasks, users
from packages.shared.crypto import encrypt_password

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes standing in for the Playwright/UniXWatcher layer (mirrors
# tests/test_worker_executor.py's fakes -- kept local/duplicated rather than
# imported, since pytest test modules aren't meant to be import targets and
# this keeps the two test files independently readable).
# ---------------------------------------------------------------------------


class FakePage:
    pass


class FakeContext:
    async def new_page(self):
        return FakePage()

    async def close(self):
        pass


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()
        self.closed = False

    async def new_context(self, **kwargs):
        return self.context

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser):
        self._browser = browser

    async def launch(self, **kwargs):
        return self._browser


class FakePlaywrightCM:
    def __init__(self, browser: FakeBrowser):
        self.chromium = FakeChromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _fake_async_playwright_factory(browser: FakeBrowser):
    def _factory():
        return FakePlaywrightCM(browser)

    return _factory


class FakeUniXWatcher:
    """Stand-in for packages.core.unix_service.UniXWatcher.

    ``results`` maps lesson_id -> outcome (a plain value returned by
    watch_unix, or an Exception instance to raise) for THAT lesson's first
    attempt; ``retry_results`` (optional) maps lesson_id -> outcome used for
    any attempt after the first, defaulting to repeating the same outcome
    (so a lesson that fails once fails the same way on its retry, matching
    the "2 ретрая -> skipped" scenarios) unless a test wants attempt-by-attempt
    control, in which case it can put a list in ``results[lesson_id]``.
    """

    results: dict = {}
    call_counts: dict = {}
    login_calls = 0

    def __init__(
        self,
        email,
        password,
        pool,
        telegram_id,
        log_callback=None,
        edit_callback=None,
        reset_video_callback=None,
        stop_event=None,
    ):
        self.email = email
        self.password = password
        self.pool = pool
        self.telegram_id = telegram_id
        self.log_callback = log_callback
        self.edit_callback = edit_callback
        self.reset_video_callback = reset_video_callback

    async def _login(self, page, email, password):
        FakeUniXWatcher.login_calls += 1
        return True

    async def watch_unix(self, page, lesson_id):
        idx = FakeUniXWatcher.call_counts.get(lesson_id, 0)
        FakeUniXWatcher.call_counts[lesson_id] = idx + 1
        outcome_spec = FakeUniXWatcher.results[lesson_id]
        outcome = outcome_spec[idx] if isinstance(outcome_spec, list) else outcome_spec
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, text, chat_id=None, message_id=None, parse_mode=None):
        self.sent.append((chat_id, text))


@pytest.fixture(autouse=True)
def _patch_browser_layer(monkeypatch):
    FakeUniXWatcher.results = {}
    FakeUniXWatcher.call_counts = {}
    FakeUniXWatcher.login_calls = 0
    monkeypatch.setattr(executor, "UniXWatcher", FakeUniXWatcher)
    monkeypatch.setattr(
        executor, "async_playwright", _fake_async_playwright_factory(FakeBrowser())
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user_with_credentials(pool, make_user, telegram_id=None):
    user_id = await make_user(telegram_id)
    await users.set_unix_credentials(
        pool, user_id, "student@example.com", encrypt_password("hunter2")
    )
    return user_id


async def _make_course(pool, course_id, lesson_ids):
    await catalog.upsert_course(pool, course_id, f"Course {course_id}", None, len(lesson_ids))
    for i, lesson_id in enumerate(lesson_ids):
        await catalog.upsert_lesson(pool, lesson_id, course_id, f"Lesson {lesson_id}", i)


async def _claim_course_task(pool, user_id, course_id):
    await tasks.enqueue(pool, user_id, "course", course_id=course_id)
    task = await tasks.claim_next(pool)
    assert task is not None
    assert task["status"] == "running"
    return task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_full_course_success_debits_n_credits_and_marks_done(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=10, reason="bonus")
    await _make_course(pool, "c1", ["l1", "l2", "l3"])
    task = await _claim_course_task(pool, user_id, "c1")

    FakeUniXWatcher.results = {
        "l1": "5/5 баллов",
        "l2": "4/5 баллов",
        "l3": "video doesn't have test",
    }

    bot = FakeBot()
    await executor.run_course(pool, bot, task)

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 7  # 10 - 3 lessons

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "done"

    progress = executor._load_progress(finished)
    assert progress["current"] == 3
    assert progress["total"] == 3
    assert len(progress["scores"]) == 3
    assert all(s["status"] == "done" for s in progress["scores"])

    watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(watch_rows) == 3
    # Each watch is debited against its own per-lesson completion marker row
    # (tasks.record_lesson_completion), NOT the course task's own id -- that's
    # what makes the lesson visible to get_watched_lesson_ids across separate
    # course-task runs (see the review-fix test below).
    assert task["id"] not in {r["task_id"] for r in watch_rows}
    marker_rows = await pool.fetch(
        "SELECT id, lesson_id, type, status FROM watch_tasks WHERE id = ANY($1::uuid[])",
        [r["task_id"] for r in watch_rows],
    )
    assert {(r["lesson_id"], r["type"], r["status"]) for r in marker_rows} == {
        ("l1", "lesson", "done"),
        ("l2", "lesson", "done"),
        ("l3", "lesson", "done"),
    }


async def test_already_watched_lessons_are_skipped(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=10, reason="bonus")
    await _make_course(pool, "c2", ["l1", "l2", "l3"])

    # Pre-seed lesson l1 as already watched: a completed lesson-type task +
    # its matching credit_ledger reason='watch' row.
    prior_task_id = await tasks.enqueue(pool, user_id, "lesson", lesson_id="l1")
    await ledger.add_entry(pool, user_id, delta=-1, reason="watch", task_id=prior_task_id)
    await tasks.finish(pool, prior_task_id, "done")

    task = await _claim_course_task(pool, user_id, "c2")

    FakeUniXWatcher.results = {"l2": "5/5 баллов", "l3": "5/5 баллов"}

    bot = FakeBot()
    await executor.run_course(pool, bot, task)

    # l1 never touched the fake browser layer at all
    assert "l1" not in FakeUniXWatcher.call_counts

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 10 - 1 - 2  # prior watch of l1 + 2 course lessons

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "done"
    progress = executor._load_progress(finished)
    assert progress["total"] == 2  # only l2, l3 counted
    assert {s["lesson_id"] for s in progress["scores"]} == {"l2", "l3"}


async def test_zero_credit_stop_mid_course(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=2, reason="bonus")
    await _make_course(pool, "c3", ["l1", "l2", "l3", "l4", "l5"])
    task = await _claim_course_task(pool, user_id, "c3")

    FakeUniXWatcher.results = {
        "l1": "5/5 баллов",
        "l2": "5/5 баллов",
        "l3": "5/5 баллов",
        "l4": "5/5 баллов",
        "l5": "5/5 баллов",
    }

    bot = FakeBot()
    await executor.run_course(pool, bot, task)

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 0  # exactly 2 credits spent on l1, l2

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "done"
    progress = executor._load_progress(finished)
    assert progress["stopped"] == "no_credits"

    watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(watch_rows) == 2
    # Debited against per-lesson completion markers, not the course task id
    # (see test_full_course_success_debits_n_credits_and_marks_done).
    assert task["id"] not in {row["task_id"] for row in watch_rows}
    watched_ids = await catalog.get_watched_lesson_ids(pool, user_id)
    assert watched_ids == {"l1", "l2"}

    # l3/l4/l5 never touched the browser layer
    assert "l3" not in FakeUniXWatcher.call_counts
    assert "l4" not in FakeUniXWatcher.call_counts
    assert "l5" not in FakeUniXWatcher.call_counts

    assert any("Кредиты закончились" in text or "credits" in text.lower() for _, text in bot.sent)


async def test_lesson_failure_is_skipped_course_continues(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=10, reason="bonus")
    await _make_course(pool, "c4", ["l1", "l2", "l3"])
    task = await _claim_course_task(pool, user_id, "c4")

    FakeUniXWatcher.results = {
        "l1": "5/5 баллов",
        "l2": ["1/5 баллов"],  # terminal low score, not retried
        "l3": "5/5 баллов",
    }

    bot = FakeBot()
    await executor.run_course(pool, bot, task)

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 8  # 10 - 2 (only l1, l3 charged)

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "done"  # course-level status unaffected by one lesson failing

    progress = executor._load_progress(finished)
    scores_by_lesson = {s["lesson_id"]: s for s in progress["scores"]}
    assert scores_by_lesson["l1"]["status"] == "done"
    assert scores_by_lesson["l2"]["status"] == "skipped"
    assert scores_by_lesson["l3"]["status"] == "done"

    watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(watch_rows) == 2  # l2 NOT charged


async def test_cancel_mid_course(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=10, reason="bonus")
    await _make_course(pool, "c5", ["l1", "l2", "l3"])
    task = await _claim_course_task(pool, user_id, "c5")

    # l1 succeeds normally; while processing l1 the API sets
    # cancel_requested (simulating POST /tasks/{id}/cancel while running) --
    # run_course re-reads progress at the TOP of each lesson iteration, so
    # this must land before l2's turn, not during it.
    class CancellingFakeUniXWatcher(FakeUniXWatcher):
        async def watch_unix(self, page, lesson_id):
            if lesson_id == "l1":
                await tasks.merge_progress(pool, task["id"], {"cancel_requested": True})
            return await super().watch_unix(page, lesson_id)

    executor.UniXWatcher = CancellingFakeUniXWatcher

    FakeUniXWatcher.results = {
        "l1": "5/5 баллов",
        "l2": "5/5 баллов",
        "l3": "5/5 баллов",
    }

    bot = FakeBot()
    await executor.run_course(pool, bot, task)

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 9  # only l1 charged before cancellation was noticed

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "cancelled"

    # l2/l3 never actually ran their watch_unix cycle after cancellation
    # was noticed at the top of the loop for l2
    assert "l2" not in FakeUniXWatcher.call_counts
    assert "l3" not in FakeUniXWatcher.call_counts

    assert any("отменён" in text for _, text in bot.sent)


async def test_course_rerun_after_topup_does_not_recharge_already_watched_lessons(
    pool, make_user
):
    """Review-fix regression test (Task 12 finding: course-watched lessons
    left no recoverable per-lesson trace, so re-running a course after
    topping up credits re-charged already-watched lessons).

    Scenario: a 5-lesson course, user starts with exactly 2 credits. Run #1
    watches l1/l2, hits zero balance, stops with progress.stopped ==
    "no_credits" (a normal 'done' outcome, per docs/plan.md). User tops up
    3 more credits and taps "Смотреть весь курс" again -- a BRAND NEW course
    task for the SAME course. Run #2 must skip l1/l2 entirely (no re-watch,
    no re-charge) and only process l3/l4/l5.
    """
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=2, reason="bonus")
    await _make_course(pool, "c6", ["l1", "l2", "l3", "l4", "l5"])

    # --- Run #1: stops at zero credits after l1, l2 ---
    task1 = await _claim_course_task(pool, user_id, "c6")
    FakeUniXWatcher.results = {
        "l1": "5/5 баллов",
        "l2": "5/5 баллов",
        "l3": "5/5 баллов",
        "l4": "5/5 баллов",
        "l5": "5/5 баллов",
    }
    bot1 = FakeBot()
    await executor.run_course(pool, bot1, task1)

    balance_after_run1 = await ledger.get_balance(pool, user_id)
    assert balance_after_run1 == 0

    finished1 = await tasks.get_task(pool, task1["id"])
    assert finished1["status"] == "done"
    progress1 = executor._load_progress(finished1)
    assert progress1["stopped"] == "no_credits"
    assert {s["lesson_id"] for s in progress1["scores"] if s["status"] == "done"} == {
        "l1",
        "l2",
    }
    assert "l3" not in FakeUniXWatcher.call_counts
    assert "l4" not in FakeUniXWatcher.call_counts
    assert "l5" not in FakeUniXWatcher.call_counts

    # --- User tops up ---
    await ledger.add_entry(pool, user_id, delta=3, reason="purchase")
    assert await ledger.get_balance(pool, user_id) == 3

    # --- Run #2: brand new course task for the SAME course + user ---
    FakeUniXWatcher.call_counts = {}  # reset call tracking for run #2
    task2 = await _claim_course_task(pool, user_id, "c6")
    assert task2["id"] != task1["id"]

    bot2 = FakeBot()
    await executor.run_course(pool, bot2, task2)

    # l1/l2 must NOT be re-watched: no new watch_unix calls for them in run #2
    assert "l1" not in FakeUniXWatcher.call_counts
    assert "l2" not in FakeUniXWatcher.call_counts
    # l3/l4/l5 ARE processed in run #2
    assert FakeUniXWatcher.call_counts.get("l3") == 1
    assert FakeUniXWatcher.call_counts.get("l4") == 1
    assert FakeUniXWatcher.call_counts.get("l5") == 1

    # Exactly 3 credits spent total in run #2 (l3, l4, l5) -- l1/l2 NOT
    # re-charged, so balance goes from 3 straight to 0, not negative.
    balance_after_run2 = await ledger.get_balance(pool, user_id)
    assert balance_after_run2 == 0

    finished2 = await tasks.get_task(pool, task2["id"])
    assert finished2["status"] == "done"
    progress2 = executor._load_progress(finished2)
    assert progress2["total"] == 3  # only l3, l4, l5 counted as "remaining"
    assert {s["lesson_id"] for s in progress2["scores"]} == {"l3", "l4", "l5"}
    assert all(s["status"] == "done" for s in progress2["scores"])

    # Total credits ever debited for 'watch' across BOTH runs == 5 (once per
    # lesson, ever) -- the core invariant this fix protects.
    all_watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(all_watch_rows) == 5

    # And get_watched_lesson_ids now reports all 5 lessons watched, visible
    # across both course-task rows.
    watched_ids = await catalog.get_watched_lesson_ids(pool, user_id)
    assert watched_ids == {"l1", "l2", "l3", "l4", "l5"}

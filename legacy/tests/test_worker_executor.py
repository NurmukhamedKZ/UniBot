"""Tests for apps/worker/executor.py's run_lesson().

Real local Postgres for everything DB-facing (task claiming, ledger,
referrals) -- same fixture pattern as the rest of the suite (see
tests/conftest.py). UniX is unreachable right now (docs say so explicitly),
so the UniXWatcher class and the Playwright browser layer are faked/
monkeypatched entirely; nothing about the fakes stands in for the DB.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.worker import executor
from packages.core.unix_service import UnixSiteUnavailableError
from packages.db import ledger, referrals, tasks, users
from packages.shared.config import REFERRAL_CREDITS
from packages.shared.crypto import encrypt_password

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes standing in for the Playwright/UniXWatcher layer
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
    """Stands in for the object returned by ``async_playwright()``."""

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

    Behavior is configured via class attributes before each call to
    run_lesson(): ``results`` is a list consumed in order across successive
    watch_unix() calls (each run_lesson() retry constructs a NEW instance,
    so the counter lives at class scope). An entry can be a plain value
    (returned) or an Exception instance (raised).
    """

    results: list = []
    call_count = 0
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
        idx = FakeUniXWatcher.call_count
        FakeUniXWatcher.call_count += 1
        outcome = FakeUniXWatcher.results[idx]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self._next_id = 1

    async def send_message(self, chat_id, text, parse_mode=None):
        msg_id = self._next_id
        self._next_id += 1
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=msg_id)

    async def edit_message_text(self, text, chat_id=None, message_id=None, parse_mode=None):
        self.sent.append((chat_id, text))


@pytest.fixture(autouse=True)
def _patch_browser_layer(monkeypatch):
    """Every test in this file uses the fakes -- patch once, reset state."""
    FakeUniXWatcher.results = []
    FakeUniXWatcher.call_count = 0
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


async def _claim_lesson_task(pool, user_id, lesson_id="123"):
    await tasks.enqueue(pool, user_id, "lesson", lesson_id=lesson_id)
    task = await tasks.claim_next(pool)
    assert task is not None
    assert task["status"] == "running"
    return task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_successful_lesson_debits_credit_and_marks_done(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=5, reason="bonus")
    task = await _claim_lesson_task(pool, user_id)

    FakeUniXWatcher.results = ["5/5 баллов"]

    bot = FakeBot()
    await executor.run_lesson(pool, bot, task)

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 4  # 5 - 1 for the watch

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "done"
    assert finished["error"] is None

    watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(watch_rows) == 1
    assert watch_rows[0]["delta"] == -1
    assert watch_rows[0]["task_id"] == task["id"]

    # user got a final outcome notification
    assert any("5/5" in text for _, text in bot.sent)


async def test_successful_first_watch_credits_referrer(pool, make_user):
    referrer_tid = 5551234
    referrer_id = await make_user(referrer_tid)
    user_id = await _make_user_with_credentials(pool, make_user)
    await referrals.register(pool, referrer_tid, user_id)
    await ledger.add_entry(pool, user_id, delta=3, reason="bonus")

    task = await _claim_lesson_task(pool, user_id)
    FakeUniXWatcher.results = ["video doesn't have test"]

    bot = FakeBot()
    await executor.run_lesson(pool, bot, task)

    referrer_balance = await ledger.get_balance(pool, referrer_id)
    assert referrer_balance == REFERRAL_CREDITS

    referral_row = await pool.fetchrow(
        "SELECT credited FROM referrals WHERE referred_id = $1", user_id
    )
    assert referral_row["credited"] is True

    # referrer got notified via the bot too
    assert any(chat_id == referrer_tid for chat_id, _ in bot.sent)


async def test_failed_lesson_after_retries_marks_failed_credit_untouched(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=2, reason="bonus")
    task = await _claim_lesson_task(pool, user_id)

    FakeUniXWatcher.results = [RuntimeError("boom-1"), RuntimeError("boom-2")]

    bot = FakeBot()
    await executor.run_lesson(pool, bot, task)

    # both attempts consumed -> 2 total attempts, matching "2 ретрая"
    assert FakeUniXWatcher.call_count == 2

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 2  # untouched

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "failed"
    assert finished["error"] is not None
    assert "boom-2" in finished["error"] or "2 attempts" in finished["error"]

    watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(watch_rows) == 0

    assert any("не списан" in text or "❌" in text for _, text in bot.sent)


async def test_unix_site_unavailable_is_distinct_failure_no_retry(pool, make_user):
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=2, reason="bonus")
    task = await _claim_lesson_task(pool, user_id)

    FakeUniXWatcher.results = [UnixSiteUnavailableError("site down")]

    bot = FakeBot()
    await executor.run_lesson(pool, bot, task)

    # no retry attempted for UnixSiteUnavailableError
    assert FakeUniXWatcher.call_count == 1

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 2  # untouched

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "failed"
    assert finished["error"] == "UniX недоступен"

    # distinct user-facing message, not the generic "failed after N attempts" one
    assert any("недоступен" in text for _, text in bot.sent)
    assert not any("attempts" in text for _, text in bot.sent)


async def test_error_result_string_is_treated_as_failure_not_success(pool, make_user):
    """watch_unix can return a plain string like "Error: ..." (a parse
    exception inside _get_test_result/_do_test) without raising -- that must
    NOT be charged as a success. Both retry attempts get the same failure
    result, so it exhausts retries like any other transient failure."""
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=2, reason="bonus")
    task = await _claim_lesson_task(pool, user_id)

    FakeUniXWatcher.results = ["Error: something", "Error: something"]

    bot = FakeBot()
    await executor.run_lesson(pool, bot, task)

    assert FakeUniXWatcher.call_count == 2

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 2  # untouched -- no credit debit

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "failed"
    assert finished["error"] is not None

    watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(watch_rows) == 0

    assert any("не списан" in text or "❌" in text for _, text in bot.sent)


async def test_low_score_is_terminal_failure_not_charged_not_retried(pool, make_user):
    """A genuinely low test score (below the 3.5 threshold, matching legacy
    watch_unix_videos's score_int >= 3.5 check) is a real failed attempt --
    not a success, and NOT worth retrying (retrying won't change the LLM's
    answers), unlike a transient exception/error-string failure."""
    user_id = await _make_user_with_credentials(pool, make_user)
    await ledger.add_entry(pool, user_id, delta=2, reason="bonus")
    task = await _claim_lesson_task(pool, user_id)

    FakeUniXWatcher.results = ["1/5 баллов"]

    bot = FakeBot()
    await executor.run_lesson(pool, bot, task)

    # NOT retried -- a low score is terminal, only one attempt consumed
    assert FakeUniXWatcher.call_count == 1

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 2  # untouched -- no credit debit

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "failed"
    assert finished["error"] is not None
    assert "1/5" in finished["error"] or "не сдан" in finished["error"]

    watch_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'watch'", user_id
    )
    assert len(watch_rows) == 0

    assert any(
        "не сдан" in text or "не списан" in text or "❌" in text for _, text in bot.sent
    )


async def test_missing_credentials_fails_without_touching_credit(pool, make_user):
    user_id = await make_user()  # no set_unix_credentials call
    await ledger.add_entry(pool, user_id, delta=2, reason="bonus")
    task = await _claim_lesson_task(pool, user_id)

    bot = FakeBot()
    await executor.run_lesson(pool, bot, task)

    balance = await ledger.get_balance(pool, user_id)
    assert balance == 2

    finished = await tasks.get_task(pool, task["id"])
    assert finished["status"] == "failed"
    assert finished["error"] == "no unix credentials"

    # never even touched the fake browser layer
    assert FakeUniXWatcher.call_count == 0

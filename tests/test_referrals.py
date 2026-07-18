import pytest

from packages.db import ledger, referrals
from packages.shared.config import REFERRAL_CREDITS

pytestmark = pytest.mark.asyncio


async def _make_referrer_and_referred(pool, make_user):
    """referrer (telegram_id known) + referred user, with a registered referral row."""
    referrer_tid = 999001
    referrer_id = await make_user(referrer_tid)
    referred_id = await make_user()
    await referrals.register(pool, referrer_tid, referred_id)
    return referrer_id, referred_id


async def test_first_watch_credits_referrer(pool, make_user):
    referrer_id, referred_id = await _make_referrer_and_referred(pool, make_user)

    balance_before = await ledger.get_balance(pool, referrer_id)

    # simulate the worker debiting the referred user's first watch, then
    # calling credit_if_first_watch right after (Task 11's flow)
    await ledger.add_entry(pool, referred_id, delta=-1, reason="watch")
    bonus_referrer_id = await referrals.credit_if_first_watch(pool, referred_id)

    assert bonus_referrer_id == referrer_id

    row = await pool.fetchrow(
        "SELECT credited FROM referrals WHERE referred_id = $1", referred_id
    )
    assert row["credited"] is True

    balance_after = await ledger.get_balance(pool, referrer_id)
    assert balance_after == balance_before + REFERRAL_CREDITS

    ledger_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'referral'",
        referrer_id,
    )
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["delta"] == REFERRAL_CREDITS


async def test_second_call_does_not_double_credit(pool, make_user):
    referrer_id, referred_id = await _make_referrer_and_referred(pool, make_user)

    await ledger.add_entry(pool, referred_id, delta=-1, reason="watch")
    first = await referrals.credit_if_first_watch(pool, referred_id)
    assert first == referrer_id

    # a second lesson watched by the same (already-credited) referred user
    await ledger.add_entry(pool, referred_id, delta=-1, reason="watch")
    second = await referrals.credit_if_first_watch(pool, referred_id)

    assert second is None

    balance = await ledger.get_balance(pool, referrer_id)
    assert balance == REFERRAL_CREDITS

    ledger_rows = await pool.fetch(
        "SELECT * FROM credit_ledger WHERE user_id = $1 AND reason = 'referral'",
        referrer_id,
    )
    assert len(ledger_rows) == 1


async def test_no_referral_is_safe_noop(pool, make_user):
    user_id = await make_user()

    await ledger.add_entry(pool, user_id, delta=-1, reason="watch")
    result = await referrals.credit_if_first_watch(pool, user_id)

    assert result is None
    # no referral row exists, no ledger row was created for anyone
    row = await pool.fetchrow("SELECT * FROM referrals WHERE referred_id = $1", user_id)
    assert row is None

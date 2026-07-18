import pytest

from packages.db import ledger

pytestmark = pytest.mark.asyncio


async def test_balance_is_sum_of_deltas(pool, make_user):
    user_id = await make_user()

    await ledger.add_entry(pool, user_id, delta=10, reason="purchase")
    await ledger.add_entry(pool, user_id, delta=-1, reason="watch")
    await ledger.add_entry(pool, user_id, delta=-1, reason="watch")

    assert await ledger.get_balance(pool, user_id) == 8


async def test_balance_zero_for_new_user(pool, make_user):
    user_id = await make_user()
    assert await ledger.get_balance(pool, user_id) == 0

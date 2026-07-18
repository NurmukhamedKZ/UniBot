import uuid

import pytest

from packages.db import ledger, payments

pytestmark = pytest.mark.asyncio


async def test_duplicate_payment_not_double_credited(pool, make_user):
    user_id = await make_user()
    external_id = f"charge_{uuid.uuid4()}"

    first = await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="tribute",
        external_id=external_id,
        package="S",
        credits=10,
        amount=1490,
        currency="KZT",
        raw={"ok": True},
    )
    second = await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="tribute",
        external_id=external_id,
        package="S",
        credits=10,
        amount=1490,
        currency="KZT",
        raw={"ok": True},
    )

    assert first is True
    assert second is False
    assert await ledger.get_balance(pool, user_id) == 10


async def test_different_external_ids_both_credit(pool, make_user):
    user_id = await make_user()

    await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="stars",
        external_id=f"charge_{uuid.uuid4()}",
        package="S",
        credits=10,
        amount=250,
        currency="XTR",
        raw={},
    )
    await payments.record_payment_and_credit(
        pool,
        user_id=user_id,
        provider="stars",
        external_id=f"charge_{uuid.uuid4()}",
        package="M",
        credits=50,
        amount=400,
        currency="XTR",
        raw={},
    )

    assert await ledger.get_balance(pool, user_id) == 60

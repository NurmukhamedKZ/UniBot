import pytest

from packages.db import tasks

pytestmark = pytest.mark.asyncio



async def test_different_users_can_both_have_active_tasks(pool, make_user):
    user1 = await make_user()
    user2 = await make_user()

    id1 = await tasks.enqueue(pool, user1, "lesson", lesson_id="l1")
    id2 = await tasks.enqueue(pool, user2, "lesson", lesson_id="l1")

    assert id1 != id2


async def test_claim_marks_running(pool, make_user):
    user_id = await make_user()
    task_id = await tasks.enqueue(pool, user_id, "lesson", lesson_id="l1")

    claimed = await tasks.claim_next(pool)

    assert claimed is not None
    assert claimed["id"] == task_id
    assert claimed["status"] == "running"
    assert claimed["started_at"] is not None

    # nothing left to claim
    assert await tasks.claim_next(pool) is None


async def test_requeue_stale(pool, make_user):
    user_id = await make_user()
    task_id = await tasks.enqueue(pool, user_id, "lesson", lesson_id="l1")
    await tasks.claim_next(pool)  # -> running

    count = await tasks.requeue_stale_running(pool)

    assert count == 1
    row = await pool.fetchrow("SELECT status, started_at FROM watch_tasks WHERE id = $1", task_id)
    assert row["status"] == "queued"
    assert row["started_at"] is None


async def test_finish_sets_status_and_error(pool, make_user):
    user_id = await make_user()
    task_id = await tasks.enqueue(pool, user_id, "lesson", lesson_id="l1")
    await tasks.claim_next(pool)

    await tasks.finish(pool, task_id, "failed", error="boom")

    row = await pool.fetchrow(
        "SELECT status, error, finished_at FROM watch_tasks WHERE id = $1", task_id
    )
    assert row["status"] == "failed"
    assert row["error"] == "boom"
    assert row["finished_at"] is not None


async def test_latest_transcribe_task(pool, make_user):
    user_id = await make_user()
    assert await tasks.latest_transcribe_task(pool, "kid1") is None

    await tasks.enqueue(pool, user_id, "transcribe", lesson_id="kid1")
    newer = await tasks.enqueue(pool, user_id, "transcribe", lesson_id="kid1")
    await tasks.enqueue(pool, user_id, "transcribe", lesson_id="kid2")

    assert (await tasks.latest_transcribe_task(pool, "kid1"))["id"] == newer

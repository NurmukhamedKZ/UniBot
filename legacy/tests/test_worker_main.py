"""Tests for apps/worker/main.py's ``_requeue_unsupported``.

Covers the Task 11 review fix: requeuing a claimed unsupported-type task
back to 'queued' without bumping ``created_at`` leaves it at the FRONT of
the FIFO queue (``claim_next`` orders ``BY created_at``), so it wins the
claim race forever and starves any runnable task queued behind it. The fix
bumps ``created_at = now()`` (and clears ``started_at``) on requeue so the
row rotates to the back of the queue.

As of Task 12, ``course`` tasks have a real executor (``run_course``) and
are no longer requeued by the main loop -- only ``transcribe`` (Task 13's
scope) still goes through ``_requeue_unsupported`` there. This test still
exercises ``_requeue_unsupported`` directly (a plain queue-repositioning
unit, independent of which types the main loop currently routes to it) using
a ``transcribe`` task, which remains unsupported today.

Real local Postgres, same fixture pattern as the rest of the suite (see
tests/conftest.py). ``main()`` itself loops forever, so this drives the
testable unit directly: ``tasks.claim_next`` + ``_requeue_unsupported`` +
``tasks.claim_next`` again, mirroring one iteration of the worker's main
loop.
"""

from __future__ import annotations

import pytest

from apps.worker.main import _requeue_unsupported
from packages.db import tasks

pytestmark = pytest.mark.asyncio


async def test_requeued_transcribe_task_moves_to_back_of_fifo_queue(pool, make_user):
    # transcribe tasks aren't subject to the "one active task per user"
    # unique index (that only covers lesson/course), but use separate users
    # anyway to mirror the realistic multi-user queue this guards against.
    transcribe_user = await make_user()
    lesson_user = await make_user()

    # Transcribe enqueued first -> older created_at -> front of the FIFO queue.
    transcribe_task_id = await tasks.enqueue(pool, transcribe_user, "transcribe", lesson_id="l0")
    lesson_task_id = await tasks.enqueue(pool, lesson_user, "lesson", lesson_id="l1")

    # One iteration of the main loop: claim the oldest queued row (the
    # transcribe task, since it's first), see it's unsupported, requeue it.
    claimed = await tasks.claim_next(pool)
    assert claimed["id"] == transcribe_task_id
    assert claimed["type"] == "transcribe"

    await _requeue_unsupported(pool, claimed)

    requeued = await tasks.get_task(pool, transcribe_task_id)
    assert requeued["status"] == "queued"
    assert requeued["started_at"] is None

    # The lesson task must now be claimable BEFORE the transcribe task is
    # re-claimed again -- i.e. the requeued transcribe task's created_at
    # moved to the back of the FIFO queue.
    next_claimed = await tasks.claim_next(pool)
    assert next_claimed["id"] == lesson_task_id
    assert next_claimed["type"] == "lesson"

    # And the transcribe task is still sitting there queued, not
    # starved-out forever, just no longer at the front.
    still_queued = await tasks.get_task(pool, transcribe_task_id)
    assert still_queued["status"] == "queued"

"""Tests for apps/worker/main.py's ``_requeue_unsupported``.

Covers the Task 11 review fix: requeuing a claimed course/transcribe task
back to 'queued' without bumping ``created_at`` leaves it at the FRONT of
the FIFO queue (``claim_next`` orders ``BY created_at``), so it wins the
claim race forever and starves any 'lesson' task queued behind it. The fix
bumps ``created_at = now()`` (and clears ``started_at``) on requeue so the
row rotates to the back of the queue.

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


async def test_requeued_course_task_moves_to_back_of_fifo_queue(pool, make_user):
    # Course and lesson tasks share the "one active task per user" unique
    # index, so they need different users to both be enqueued at once.
    course_user = await make_user()
    lesson_user = await make_user()

    # Course enqueued first -> older created_at -> front of the FIFO queue.
    course_task_id = await tasks.enqueue(pool, course_user, "course", course_id="c1")
    lesson_task_id = await tasks.enqueue(pool, lesson_user, "lesson", lesson_id="l1")

    # One iteration of the main loop: claim the oldest queued row (the
    # course task, since it's first), see it's unsupported, requeue it.
    claimed = await tasks.claim_next(pool)
    assert claimed["id"] == course_task_id
    assert claimed["type"] == "course"

    await _requeue_unsupported(pool, claimed)

    requeued = await tasks.get_task(pool, course_task_id)
    assert requeued["status"] == "queued"
    assert requeued["started_at"] is None

    # The lesson task must now be claimable BEFORE the course task is
    # re-claimed again -- i.e. the requeued course task's created_at moved
    # to the back of the FIFO queue.
    next_claimed = await tasks.claim_next(pool)
    assert next_claimed["id"] == lesson_task_id
    assert next_claimed["type"] == "lesson"

    # And the course task is still sitting there queued, not starved-out
    # forever, just no longer at the front.
    still_queued = await tasks.get_task(pool, course_task_id)
    assert still_queued["status"] == "queued"

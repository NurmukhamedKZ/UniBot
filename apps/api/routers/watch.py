"""Watch + tasks endpoints: paid autowatch job submission and control.

Per docs/plan.md Task 9. Sits behind ``current_user`` like every other
router. Judgment calls (also restated in the Task 9 report):

- ``GET /tasks/active`` returns ``204 No Content`` (empty body) when the
  user has no active lesson/course task, and ``200 {...task...}`` otherwise
  — trivial for the webapp poller to branch on (``if (resp.status === 204)``)
  without having to special-case a ``null`` JSON body.
- ``POST /tasks/{id}/cancel`` on a task belonging to a different user
  returns 404 (not 403): this avoids confirming *that a task with that id
  exists* to a caller who doesn't own it (standard reason to prefer 404
  over 403 for object-scoped endpoints keyed by opaque ids the client
  doesn't otherwise get to enumerate).
- Cancelling an already-terminal task (done/failed/cancelled) is a no-op
  200 returning the task's current state, not an error — cancel is
  idempotent-in-spirit ("make sure this task is not going to run/keep
  running"), and a terminal task already satisfies that.
"""

from __future__ import annotations

import json

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from apps.api.auth import current_user
from packages.db import ledger, tasks

router = APIRouter()


def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _task_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    # asyncpg returns jsonb columns as raw JSON strings (no codec registered
    # on this pool) -- decode so the API returns a real JSON object, not a
    # double-encoded string, matching what update_progress/merge_progress wrote.
    if isinstance(d.get("progress"), str):
        d["progress"] = json.loads(d["progress"])
    return d


@router.post("/watch", status_code=201)
async def create_watch_task(request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    body = await request.json()

    watch_type = body.get("type")
    watch_id = body.get("id")
    if watch_type not in ("lesson", "course") or not watch_id:
        raise HTTPException(422, "body must be {type: 'lesson'|'course', id: str}")

    if user["unix_email"] is None or user["unix_password_enc"] is None:
        return JSONResponse(status_code=409, content={"error": "no_credentials"})

    balance = await ledger.get_balance(pool, user["id"])
    if balance <= 0:
        return JSONResponse(status_code=402, content={"error": "no_credits"})

    try:
        task_id = await tasks.enqueue(
            pool,
            user_id=user["id"],
            type=watch_type,
            lesson_id=watch_id if watch_type == "lesson" else None,
            course_id=watch_id if watch_type == "course" else None,
        )
    except tasks.TaskAlreadyActiveError:
        return JSONResponse(status_code=409, content={"error": "task_already_active"})

    return {"task_id": str(task_id)}


@router.get("/tasks/active")
async def get_active_task(request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    task = await tasks.get_active_task(pool, user["id"])
    if task is None:
        return Response(status_code=204)
    return _task_to_dict(task)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    try:
        task = await tasks.get_task(pool, task_id)
    except (asyncpg.DataError, ValueError):
        # malformed UUID in the path -> indistinguishable from "not found"
        raise HTTPException(404, "task not found")

    if task is None or str(task["user_id"]) != str(user["id"]):
        raise HTTPException(404, "task not found")

    if task["status"] == "queued":
        await tasks.finish(pool, task_id, "cancelled")
    elif task["status"] == "running":
        await tasks.merge_progress(pool, task_id, {"cancel_requested": True})
    # else: already done/failed/cancelled -> no-op, fall through to re-fetch+return

    updated = await tasks.get_task(pool, task_id)
    return _task_to_dict(updated)

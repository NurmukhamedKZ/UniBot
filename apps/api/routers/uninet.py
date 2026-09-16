"""Курсы/недели/видео uninet от имени юзера + автопросмотр + транскрибы.

Каталог личный: всё читается вживую из uninet по кредам юзера (у каждого
студента свои курсы). Транскрибы кэшируются в нашей БД по id видео Kinescope
и общие для всех.
"""

from __future__ import annotations

import asyncio
import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.api.auth import current_user
from packages.core import uninet
from packages.db import catalog, tasks
from packages.shared.crypto import decrypt_password

router = APIRouter()

_KID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")


class NoCredentials(Exception):
    pass


def _session(user) -> uninet.Uninet:
    if user["kbtu_email"] is None or user["kbtu_password_enc"] is None:
        raise NoCredentials()
    return uninet.session(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]))


def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _kid(kid: str) -> str:
    if not _KID_RE.match(kid):
        raise HTTPException(404, "video not found")
    return kid


@router.get("/courses")
async def list_courses(user=Depends(current_user)):
    return await _session(user).courses()


@router.get("/courses/{course_id}")
async def get_course(course_id: int, user=Depends(current_user)):
    data = await _session(user).course(course_id)
    return {"course": data["course"], "weeks": data["weeks"]}


@router.get("/weeks/{week_id}")
async def get_week(week_id: int, request: Request, user=Depends(current_user)):
    data = await _session(user).week(week_id)
    videos = uninet.week_videos(data)
    kids = [uninet.kinescope_id(v["url"]) for v in videos]
    done = await catalog.transcribed_ids(_pool(request), kids)
    return {
        "week": data["week"],
        "videos": [
            {
                "id": v["id"],
                "title": v["title"],
                "duration_sec": v["durationSec"],
                "completed": v["completed"],
                "kinescope_id": kid,
                "has_transcript": kid in done,
            }
            for v, kid in zip(videos, kids)
        ],
    }


async def _watch_week(s: uninet.Uninet, week_id: int) -> int:
    pending = [v for v in uninet.week_videos(await s.week(week_id)) if not v["completed"]]
    await asyncio.gather(*(s.mark_watched(v) for v in pending))
    return len(pending)


@router.post("/weeks/{week_id}/watch")
async def watch_week(week_id: int, user=Depends(current_user)):
    return {"marked": await _watch_week(_session(user), week_id)}


@router.post("/courses/{course_id}/watch")
async def watch_course(course_id: int, user=Depends(current_user)):
    s = _session(user)
    weeks = [w["id"] for w in (await s.course(course_id))["weeks"] if w["unlocked"]]
    marked = 0
    for week_id in weeks:  # по неделе за раз, чтобы не долбить uninet десятками параллельных POST
        marked += await _watch_week(s, week_id)
    return {"marked": marked}


async def _transcript_state(pool: asyncpg.Pool, kid: str) -> dict:
    text = await catalog.get_transcript(pool, kid)
    if text is not None:
        return {"transcript": text, "status": "done"}
    task = await tasks.latest_transcribe_task(pool, kid)
    return {"transcript": None, "status": task["status"] if task else None}


@router.get("/videos/{kid}/transcript")
async def get_transcript(kid: str, request: Request, _user=Depends(current_user)):
    return await _transcript_state(_pool(request), _kid(kid))


@router.post("/videos/{kid}/transcribe", status_code=202)
async def transcribe(kid: str, request: Request, user=Depends(current_user)):
    pool = _pool(request)
    state = await _transcript_state(pool, _kid(kid))
    if state["status"] == "done":
        return JSONResponse(status_code=200, content=state)
    if state["status"] not in ("queued", "running"):
        await tasks.enqueue(pool, user_id=user["id"], type="transcribe", lesson_id=kid)
        state["status"] = "queued"
    return state

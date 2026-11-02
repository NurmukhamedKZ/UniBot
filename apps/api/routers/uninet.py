"""Курсы/недели/видео uninet от имени юзера + автопросмотр + транскрибы.

Автопросмотр — очередь ``watch_tasks type='watch'`` (одна задача на неделю):
uninet засчитывает только реальное время, воркер смотрит видео десятки минут.

Каталог личный: с кредами всё читается вживую из uninet (у каждого студента
свои курсы) и попутно кэшируется в БД. Без кредов отдаём кэш — все курсы,
которые кто-то уже открывал, — чтобы копировать готовые транскрибы; то, чего
в кэше нет, и новые транскрибы требуют входа (409 no_credentials).
Транскрибы кэшируются по id видео Kinescope и общие для всех.
"""

from __future__ import annotations

import logging
import re

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.api.auth import current_user
from packages.core import uninet
from packages.db import catalog, tasks
from packages.shared.crypto import decrypt_password

router = APIRouter()

_KID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")


class NoCredentials(Exception):
    pass


def _has_credentials(user) -> bool:
    return user["kbtu_email"] is not None and user["kbtu_password_enc"] is not None


def _session(user) -> uninet.Uninet:
    if not _has_credentials(user):
        raise NoCredentials()
    return uninet.session(user["kbtu_email"], decrypt_password(user["kbtu_password_enc"]))


def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _kid(kid: str) -> str:
    if not _KID_RE.match(kid):
        raise HTTPException(404, "video not found")
    return kid


@router.get("/courses")
async def list_courses(request: Request, user=Depends(current_user)):
    """Свои курсы (если есть креды) + все остальные, что уже закэшированы в базе."""
    pool = _pool(request)
    if not _has_credentials(user):
        return {"mine": None, "others": await catalog.cached_courses(pool)}
    courses = await _session(user).courses()
    await catalog.cache_courses(pool, courses)
    mine_ids = {c["id"] for c in courses}
    others = [c for c in await catalog.cached_courses(pool) if c["id"] not in mine_ids]
    return {"mine": courses, "others": others}


async def _cache_week(pool: asyncpg.Pool, week_id: int, data: dict) -> tuple[dict, list[dict]]:
    """Ответ uninet по неделе -> (week, videos) для API; попутно кладёт неделю в кэш."""
    videos = [
        {
            "id": v["id"],
            "title": v["title"],
            "duration_sec": v["durationSec"],
            "completed": v["completed"],
            "kinescope_id": uninet.kinescope_id(v["url"]),
            "watched_sec": v.get("cumulativeWatchSec"),
            "required_sec": v.get("requiredSec"),
        }
        for v in uninet.week_videos(data)
    ]
    await catalog.cache_week(pool, week_id, data["week"],
                             [{**v, "completed": False, "watched_sec": None} for v in videos])
    return data["week"], videos


async def _cache_missing_weeks(pool: asyncpg.Pool, s: uninet.Uninet, weeks: list[dict]) -> None:
    """Чтобы без кредов были видны все недели курса, а не только открытые кем-то вручную."""
    cached = await catalog.cached_week_ids(pool, [w["id"] for w in weeks])
    for w in weeks:  # по одной, не долбим uninet параллельно
        if w["unlocked"] and w["id"] not in cached:
            try:
                await _cache_week(pool, w["id"], await s.week(w["id"]))
            except Exception:
                logging.getLogger(__name__).warning("caching week %s failed", w["id"], exc_info=True)


async def _cached_course(pool: asyncpg.Pool, course_id: int, miss: Exception) -> dict:
    weeks = await catalog.cached_course_weeks(pool, course_id)
    if weeks is None:
        raise miss
    return {"course": {"id": course_id}, "weeks": weeks}


@router.get("/courses/{course_id}")
async def get_course(course_id: int, request: Request, background: BackgroundTasks, user=Depends(current_user)):
    pool = _pool(request)
    if not _has_credentials(user):
        return await _cached_course(pool, course_id, NoCredentials())
    s = _session(user)
    try:
        data = await s.course(course_id)
    except uninet.UninetUnavailable as exc:  # чужой курс uninet не отдаёт (422) — показываем кэш
        return await _cached_course(pool, course_id, exc)
    await catalog.cache_course_weeks(pool, course_id, data["weeks"])
    background.add_task(_cache_missing_weeks, pool, s, data["weeks"])
    return {"course": data["course"], "weeks": data["weeks"]}


@router.get("/weeks/{week_id}")
async def get_week(week_id: int, request: Request, user=Depends(current_user)):
    pool = _pool(request)
    try:
        if not _has_credentials(user):
            raise NoCredentials()
        week, videos = await _cache_week(pool, week_id, await _session(user).week(week_id))
    except (NoCredentials, uninet.UninetUnavailable):  # чужая неделя — из кэша
        cached = await catalog.cached_week(pool, week_id)
        if cached is None:
            raise
        week, videos = cached
    done = await catalog.transcribed_ids(pool, [v["kinescope_id"] for v in videos])
    watch = await tasks.latest_watch_task(pool, user["id"], week_id)
    return {
        "week": week,
        "videos": [{**v, "has_transcript": v["kinescope_id"] in done} for v in videos],
        "watch_status": watch["status"] if watch else None,
    }


@router.post("/weeks/{week_id}/watch", status_code=202)
async def watch_week(week_id: int, request: Request, user=Depends(current_user)):
    _session(user)  # 409 без кредов
    await tasks.enqueue(_pool(request), user["id"], "watch", lesson_id=str(week_id))
    return {"status": "queued"}


@router.post("/courses/{course_id}/watch", status_code=202)
async def watch_course(course_id: int, request: Request, user=Depends(current_user)):
    """Все открытые недели — в очередь; досмотренные воркер пропустит сам."""
    pool = _pool(request)
    weeks = [w["id"] for w in (await _session(user).course(course_id))["weeks"] if w["unlocked"]]
    for week_id in weeks:
        await tasks.enqueue(pool, user["id"], "watch", lesson_id=str(week_id))
    return {"queued": len(weeks)}


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
        if not _has_credentials(user):  # новые транскрибы (OpenAI) — только вошедшим
            raise NoCredentials()
        await tasks.enqueue(pool, user_id=user["id"], type="transcribe", lesson_id=kid)
        state["status"] = "queued"
    return state

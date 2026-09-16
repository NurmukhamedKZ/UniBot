"""Catalog + content endpoints: courses, lessons, transcripts, questions.

Per docs/spec.md, transcripts and Q&A are "public" (free, no credits, no
UniX-credential requirement) but every request still requires a valid
Telegram initData header — all routes here sit behind ``current_user``
just like Task 7's dependency model, they just never check the user's
credit balance or UniX credentials.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.api.auth import current_user
from packages.db import catalog, tasks

router = APIRouter()


def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


@router.get("/courses")
async def list_courses(request: Request, _user=Depends(current_user)):
    pool = _get_pool(request)
    courses = await catalog.list_courses(pool)
    return [dict(row) for row in courses]


@router.get("/courses/{course_id}")
async def get_course_lessons(course_id: str, request: Request, _user=Depends(current_user)):
    pool = _get_pool(request)
    lessons = await catalog.get_course_lessons(pool, course_id)
    return [dict(row) for row in lessons]


@router.get("/lessons/{lesson_id}")
async def get_lesson_content(lesson_id: str, request: Request, _user=Depends(current_user)):
    pool = _get_pool(request)
    content = await catalog.get_lesson_content(pool, lesson_id)
    if content["title"] is None:
        raise HTTPException(404, "lesson not found")
    return content


@router.post("/lessons/{lesson_id}/transcribe", status_code=202)
async def transcribe_lesson(lesson_id: str, request: Request, user=Depends(current_user)):
    pool = _get_pool(request)
    existing = await catalog.get_transcript(pool, lesson_id)
    if existing is not None:
        # Already cached: return it immediately with 200, overriding the
        # route's default 202 (which only applies to the enqueue path below).
        return JSONResponse(status_code=200, content={"transcript": existing})

    task_id = await tasks.enqueue(pool, user_id=user["id"], type="transcribe", lesson_id=lesson_id)
    return {"task_id": str(task_id)}

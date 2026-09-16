"""Кэш транскрибов. Ключ ``lesson_id`` = id видео Kinescope (уникален глобально)."""

from __future__ import annotations

import json

import asyncpg


async def ensure_lesson_exists(pool_or_conn, lesson_id: str) -> None:
    """``lessons.lesson_id`` — FK-цель для transcripts; вставить пустую строку, если нет."""
    await pool_or_conn.execute(
        "INSERT INTO lessons (lesson_id) VALUES ($1) ON CONFLICT (lesson_id) DO NOTHING",
        lesson_id,
    )


async def get_transcript(pool: asyncpg.Pool, lesson_id: str) -> str | None:
    return await pool.fetchval("SELECT text FROM transcripts WHERE lesson_id = $1", lesson_id)


async def transcribed_ids(pool: asyncpg.Pool, lesson_ids: list[str]) -> set[str]:
    rows = await pool.fetch("SELECT lesson_id FROM transcripts WHERE lesson_id = ANY($1::text[])", lesson_ids)
    return {r["lesson_id"] for r in rows}


async def save_transcript(pool: asyncpg.Pool, lesson_id: str, text: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_lesson_exists(conn, lesson_id)
            await conn.execute(
                """
                INSERT INTO transcripts (lesson_id, text)
                VALUES ($1, $2)
                ON CONFLICT (lesson_id) DO UPDATE SET text = EXCLUDED.text
                """,
                lesson_id,
                text,
            )


# --- Кэш каталога uninet (для юзеров без кредов) ---


async def cache_courses(pool: asyncpg.Pool, courses: list[dict]) -> None:
    await pool.executemany(
        """
        INSERT INTO uninet_courses (id, info) VALUES ($1, $2::jsonb)
        ON CONFLICT (id) DO UPDATE SET info = EXCLUDED.info, updated_at = now()
        """,
        [(c["id"], json.dumps(c)) for c in courses],
    )


async def cache_course_weeks(pool: asyncpg.Pool, course_id: int, weeks: list[dict]) -> None:
    await pool.execute(
        """
        INSERT INTO uninet_courses (id, weeks) VALUES ($1, $2::jsonb)
        ON CONFLICT (id) DO UPDATE SET weeks = EXCLUDED.weeks, updated_at = now()
        """,
        course_id,
        json.dumps(weeks),
    )


async def cache_week(pool: asyncpg.Pool, week_id: int, week: dict, videos: list[dict]) -> None:
    await pool.execute(
        """
        INSERT INTO uninet_weeks (id, week, videos) VALUES ($1, $2::jsonb, $3::jsonb)
        ON CONFLICT (id) DO UPDATE SET week = EXCLUDED.week, videos = EXCLUDED.videos, updated_at = now()
        """,
        week_id,
        json.dumps(week),
        json.dumps(videos),
    )


async def cached_courses(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("SELECT info FROM uninet_courses WHERE info IS NOT NULL ORDER BY info->>'name'")
    return [json.loads(r["info"]) for r in rows]


async def cached_course_weeks(pool: asyncpg.Pool, course_id: int) -> list[dict] | None:
    """Только недели, которые есть в кэше (остальные без кредов не открыть)."""
    raw = await pool.fetchval("SELECT weeks FROM uninet_courses WHERE id = $1", course_id)
    if raw is None:
        return None
    weeks = json.loads(raw)
    rows = await pool.fetch("SELECT id FROM uninet_weeks WHERE id = ANY($1::int[])", [w["id"] for w in weeks])
    ids = {r["id"] for r in rows}
    return [w for w in weeks if w["id"] in ids]


async def cached_week(pool: asyncpg.Pool, week_id: int) -> tuple[dict, list[dict]] | None:
    row = await pool.fetchrow("SELECT week, videos FROM uninet_weeks WHERE id = $1", week_id)
    return (json.loads(row["week"]), json.loads(row["videos"])) if row else None

"""Кэш транскрибов. Ключ ``lesson_id`` = id видео Kinescope (уникален глобально)."""

from __future__ import annotations

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

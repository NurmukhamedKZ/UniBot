"""courses/lessons/transcripts/questions — public catalog content."""

from __future__ import annotations

import json

import asyncpg


async def ensure_lesson_exists(pool_or_conn, lesson_id: str) -> None:
    """Insert a bare lesson row if one doesn't exist yet.

    ``lessons.lesson_id`` is the FK target for transcripts/questions, but the
    worker can encounter a lesson (via a user's own watch, or transcription)
    before the daily catalog scrape has upserted it — call this first so the
    FK insert doesn't fail (mirrors the legacy bot's ``ensure_unix_video_exists``).
    """
    await pool_or_conn.execute(
        "INSERT INTO lessons (lesson_id) VALUES ($1) ON CONFLICT (lesson_id) DO NOTHING",
        lesson_id,
    )


async def save_questions(pool: asyncpg.Pool, lesson_id: str, questions: list[dict]) -> None:
    """Replace all stored questions for a lesson.

    ``questions``: ``[{question, answers: list[str], llm_answer_index: int|None}]``.
    A repeat autopilot run of the same lesson can produce a different set of
    questions (UniX rotates its question bank) — delete-then-insert keeps
    only the latest run's questions rather than accumulating duplicates.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_lesson_exists(conn, lesson_id)
            await conn.execute("DELETE FROM questions WHERE lesson_id = $1", lesson_id)
            if questions:
                await conn.executemany(
                    """
                    INSERT INTO questions (lesson_id, question, answers, llm_answer_index)
                    VALUES ($1, $2, $3::jsonb, $4)
                    """,
                    [
                        (
                            lesson_id,
                            q["question"],
                            json.dumps(q["answers"]),
                            q.get("llm_answer_index"),
                        )
                        for q in questions
                    ],
                )


async def get_transcript(pool: asyncpg.Pool, lesson_id: str) -> str | None:
    return await pool.fetchval(
        "SELECT text FROM transcripts WHERE lesson_id = $1", lesson_id
    )


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


async def get_lesson_content(pool: asyncpg.Pool, lesson_id: str) -> dict:
    async with pool.acquire() as conn:
        lesson = await conn.fetchrow(
            "SELECT title FROM lessons WHERE lesson_id = $1", lesson_id
        )
        transcript = await conn.fetchval(
            "SELECT text FROM transcripts WHERE lesson_id = $1", lesson_id
        )
        question_rows = await conn.fetch(
            "SELECT question, answers, llm_answer_index FROM questions "
            "WHERE lesson_id = $1 ORDER BY id",
            lesson_id,
        )
    return {
        "title": lesson["title"] if lesson else None,
        "transcript": transcript,
        "questions": [
            {
                "question": row["question"],
                "answers": json.loads(row["answers"]) if isinstance(row["answers"], str) else row["answers"],
                "llm_answer_index": row["llm_answer_index"],
            }
            for row in question_rows
        ],
    }


async def upsert_course(
    pool: asyncpg.Pool, course_id: str, title: str, cover: str | None, lessons_count: int | None
) -> None:
    await pool.execute(
        """
        INSERT INTO courses (course_id, title, cover, lessons_count, scraped_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (course_id) DO UPDATE
            SET title = EXCLUDED.title,
                cover = EXCLUDED.cover,
                lessons_count = EXCLUDED.lessons_count,
                scraped_at = now()
        """,
        course_id,
        title,
        cover,
        lessons_count,
    )


async def upsert_lesson(
    pool: asyncpg.Pool, lesson_id: str, course_id: str, title: str | None, position: int | None
) -> None:
    await pool.execute(
        """
        INSERT INTO lessons (lesson_id, course_id, title, position)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (lesson_id) DO UPDATE
            SET course_id = EXCLUDED.course_id,
                title = EXCLUDED.title,
                position = EXCLUDED.position
        """,
        lesson_id,
        course_id,
        title,
        position,
    )


async def list_courses(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM courses ORDER BY title")


async def get_watched_lesson_ids(pool: asyncpg.Pool, user_id) -> set[str]:
    """Lesson ids ``user_id`` has already successfully watched (and been
    credited for) — used by ``run_course`` (apps/worker/executor.py, Task 12)
    to skip lessons a course-batch run would otherwise re-charge.

    A lesson counts as watched if there is a ``credit_ledger`` row with
    ``reason='watch'`` for this user whose ``task_id`` points to a
    ``watch_tasks`` row carrying that ``lesson_id`` — i.e. a completed,
    credited ``run_lesson`` (or a prior ``run_course`` lesson-attempt: both
    debit through the same ``reason='watch'`` ``credit_ledger`` insert, see
    ``run_lesson``/``run_course``). Not scoped to a single course_id since a
    lesson_id is only ever watched once regardless of which course listing
    it's looked up through.
    """
    rows = await pool.fetch(
        """
        SELECT DISTINCT wt.lesson_id
        FROM credit_ledger cl
        JOIN watch_tasks wt ON wt.id = cl.task_id
        WHERE cl.user_id = $1 AND cl.reason = 'watch' AND wt.lesson_id IS NOT NULL
        """,
        user_id,
    )
    return {row["lesson_id"] for row in rows}


async def get_course_lessons(pool: asyncpg.Pool, course_id: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT
            l.*,
            (t.lesson_id IS NOT NULL) AS has_transcript,
            COALESCE(q.questions_count, 0) AS questions_count
        FROM lessons l
        LEFT JOIN transcripts t ON t.lesson_id = l.lesson_id
        LEFT JOIN (
            SELECT lesson_id, COUNT(*) AS questions_count
            FROM questions
            GROUP BY lesson_id
        ) q ON q.lesson_id = l.lesson_id
        WHERE l.course_id = $1
        ORDER BY l.position NULLS LAST
        """,
        course_id,
    )

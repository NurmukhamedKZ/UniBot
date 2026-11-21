"""Каталог uninet в боте: /courses, /course, /week, /watch, /transcribe + кнопки.

Числа в командах — позиции из последнего показанного списка (контекст в
``apps.bot.common``). Кнопки несут реальные id в ``callback_data``.
"""

from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from packages.core import uninet
from packages.core.uninet import InvalidCredentials, UninetUnavailable
from packages.db import catalog, tasks
from packages.shared.crypto import decrypt_password

from apps.bot.common import (
    LOGIN_HINT,
    Context,
    context,
    credentials,
    edit,
    esc,
    fmt_duration,
    typing,
    uninet_error,
    user_row,
)

router = Router()


# --- Сессия uninet -----------------------------------------------------------------


async def _session(pool: asyncpg.Pool, telegram_id: int) -> uninet.Uninet | None:
    creds = await credentials(pool, telegram_id)
    if creds is None:
        return None
    return uninet.session(creds["kbtu_email"], decrypt_password(creds["kbtu_password_enc"]))


async def _load_course(pool, s: uninet.Uninet, ctx: Context, course_id: int):
    data = await s.course(course_id)
    await catalog.cache_course_weeks(pool, course_id, data["weeks"])
    ctx.course_id, ctx.course, ctx.weeks = course_id, data["course"], data["weeks"]
    ctx.week_id, ctx.week, ctx.videos = None, None, []
    return data["course"], data["weeks"]


async def _ensure_course(pool, s: uninet.Uninet, ctx: Context, course_id: int) -> dict:
    if ctx.course_id == course_id and ctx.course and ctx.weeks:
        return ctx.course
    course, _ = await _load_course(pool, s, ctx, course_id)
    return course


async def _load_week(pool, s: uninet.Uninet, ctx: Context, week_id: int):
    data = await s.week(week_id)
    videos = uninet.week_videos(data)
    # Кэш нужен Mini App и /status (там по week id показываем тему недели).
    await catalog.cache_week(pool, week_id, data["week"], [
        {
            "id": v["id"],
            "title": v["title"],
            "duration_sec": v["durationSec"],
            "completed": v["completed"],
            "kinescope_id": uninet.kinescope_id(v["url"]),
            "watched_sec": v.get("cumulativeWatchSec"),
            "required_sec": v.get("requiredSec"),
        }
        for v in videos
    ])
    ctx.week_id, ctx.week, ctx.videos = week_id, data["week"], videos
    return data["week"], videos


def _kid(video: dict) -> str | None:
    url = video.get("url")
    return uninet.kinescope_id(url) if url else None


# --- Рендер ------------------------------------------------------------------------


def _short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def courses_text(courses: list[dict]) -> str:
    if not courses:
        return "Курсов не нашлось. Возможно, семестр ещё не начался."
    lines = ["<b>📚 Твои курсы</b>", ""]
    for i, course in enumerate(courses, 1):
        line = f"{i}. <b>{esc(course.get('name'))}</b>"
        if course.get("code"):
            line += f" · {esc(course['code'])}"
        lines.append(line)
        if course.get("teacher"):
            lines.append(f"    {esc(course['teacher'])}")
    lines += ["", "Открой: /course 1"]
    return "\n".join(lines)


def courses_keyboard(courses: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=_short(f"{i}. {course.get('name')}"),
            callback_data=f"c:{course['id']}",
        )]
        for i, course in enumerate(courses, 1)
    ]
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def weeks_text(course: dict, weeks: list[dict]) -> str:
    header = f"<b>📚 {esc(course.get('name'))}</b>"
    if course.get("code"):
        header += f" · {esc(course['code'])}"
    lines = [header]
    if course.get("teacher"):
        lines.append(f"Преподаватель: {esc(course['teacher'])}")
    lines.append("")
    if not weeks:
        lines.append("Недель пока нет.")
    for i, week in enumerate(weeks, 1):
        icon = "🔓" if week.get("unlocked") else "🔒"
        theme = f" · {esc(week['theme'])}" if week.get("theme") else ""
        lines.append(f"[{i}] Неделя {week.get('weekNumber', '?')} {icon}{theme}")
    lines += [
        "",
        "Неделя открывается по номеру в списке: /week 1",
        "Смотреть: /watch 1 · весь курс: /watch all",
    ]
    return "\n".join(lines)


def weeks_keyboard(course_id: int, weeks: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=_short(f"Неделя {week.get('weekNumber', '?')} · {week.get('theme') or ''}".rstrip(" ·")),
            callback_data=f"w:{course_id}:{week['id']}",
        )]
        for week in weeks if week.get("unlocked")
    ]
    if any(week.get("unlocked") for week in weeks):
        rows.append([InlineKeyboardButton(text="▶️ Смотреть весь курс", callback_data=f"wc:{course_id}")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def week_text(course: dict, week: dict, videos: list[dict]) -> str:
    lines = [f"<b>🗓 {esc(course.get('name'))} · Неделя {week.get('weekNumber', '?')}</b>"]
    if week.get("theme"):
        lines.append(esc(week["theme"]))
    if week.get("dueDate"):
        lines.append(f"Дедлайн: {esc(str(week['dueDate'])[:10])}")
    lines.append("")
    if not videos:
        lines.append("Видео в этой неделе нет.")
    for i, video in enumerate(videos, 1):
        icon = "✅" if video.get("completed") else "▶️"
        lines.append(f"[{i}] {icon} {esc(video.get('title'))} · {fmt_duration(video.get('durationSec'))}")
    lines += ["", "Транскриб: /transcribe 1"]
    return "\n".join(lines)


def week_keyboard(course_id: int, week_id: int, videos: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, video in enumerate(videos, 1):
        kid = _kid(video)
        data = f"v:{course_id}:{week_id}:{kid}"
        if kid and len(data.encode()) <= 64:  # лимит Telegram на callback_data
            rows.append([InlineKeyboardButton(
                text=_short(f"{i}. {video.get('title')}"),
                callback_data=data,
            )])
    rows.append([InlineKeyboardButton(text="▶️ Смотреть неделю", callback_data=f"wa:{week_id}")])
    rows.append([
        InlineKeyboardButton(text="⬅️ К курсу", callback_data=f"c:{course_id}"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def video_text(video: dict) -> str:
    lines = [
        f"<b>🎬 {esc(video.get('title'))}</b>",
        "",
        f"Длительность: {fmt_duration(video.get('durationSec'))}",
    ]
    if video.get("requiredSec") is not None:
        lines.append(
            f"Просмотрено: {fmt_duration(video.get('cumulativeWatchSec'))} / "
            f"{fmt_duration(video.get('requiredSec'))}"
        )
    lines.append("Статус: " + ("✅ просмотрено" if video.get("completed") else "не просмотрено"))
    return "\n".join(lines)


def video_keyboard(course_id: int, week_id: int, kid: str) -> InlineKeyboardMarkup:
    rows = []
    transcript = f"t:{kid}"
    if len(transcript.encode()) <= 64:  # лимит Telegram на callback_data
        rows.append([InlineKeyboardButton(text="📝 Транскриб", callback_data=transcript)])
    rows.append([
        InlineKeyboardButton(text="⬅️ К неделе", callback_data=f"w:{course_id}:{week_id}"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- Транскрибы --------------------------------------------------------------------


def transcript_file(kid: str, text: str) -> BufferedInputFile:
    return BufferedInputFile(text.encode("utf-8"), filename=f"transcript-{kid[:12]}.txt")


async def send_transcript(message: Message, pool: asyncpg.Pool, row: asyncpg.Record, kid: str) -> None:
    """Готовый транскриб — файлом; иначе ставим в очередь (нужны креды)."""
    text = await catalog.get_transcript(pool, kid)
    if text is not None:
        await message.answer_document(transcript_file(kid, text), caption="📝 Транскриб")
        return
    if row["kbtu_email"] is None:
        await message.answer("Готового транскриба нет. Войди /login, чтобы сделать новый.")
        return
    task = await tasks.latest_transcribe_task(pool, kid)
    if task and task["status"] in ("queued", "running"):
        if task["user_id"] == row["id"]:
            await message.answer("Транскриб уже готовится — пришлю файлом, когда будет готов.")
        else:
            await message.answer(
                "Транскриб уже готовится другим запросом. Повтори /transcribe "
                "через несколько минут — файл придёт сразу."
            )
        return
    await tasks.enqueue(pool, user_id=row["id"], type="transcribe", lesson_id=kid)
    await message.answer("📝 Готовлю транскриб — пришлю файлом, когда будет готов.")


# --- Команды -----------------------------------------------------------------------


def _index(arg: str | None) -> int | None:
    """``"2"`` → 1 (индекс в списке); всё остальное → None."""
    try:
        n = int((arg or "").strip())
    except ValueError:
        return None
    return n - 1 if n >= 1 else None


async def _enqueue_watch(pool: asyncpg.Pool, user_id, week_ids: list[int]) -> int:
    queued = 0
    for week_id in week_ids:
        if await tasks.enqueue(pool, user_id, "watch", str(week_id)) is not None:
            queued += 1
    return queued


@router.message(Command("courses"))
async def cmd_courses(message: Message, pool: asyncpg.Pool) -> None:
    await typing(message)
    s = await _session(pool, message.from_user.id)
    if s is None:
        await message.answer(LOGIN_HINT)
        return
    try:
        courses = await s.courses()
    except (InvalidCredentials, UninetUnavailable) as exc:
        await message.answer(uninet_error(exc))
        return
    await catalog.cache_courses(pool, courses)
    ctx = context(message.from_user.id)
    ctx.courses, ctx.course_id, ctx.course = courses, None, None
    ctx.weeks, ctx.week_id, ctx.week, ctx.videos = [], None, None, []
    await message.answer(courses_text(courses), reply_markup=courses_keyboard(courses))


@router.message(Command("course"))
async def cmd_course(message: Message, command: CommandObject, pool: asyncpg.Pool) -> None:
    ctx = context(message.from_user.id)
    idx = _index(command.args)
    if idx is None or idx >= len(ctx.courses):
        await message.answer("Сначала /courses, потом /course N.")
        return
    await typing(message)
    s = await _session(pool, message.from_user.id)
    if s is None:
        await message.answer(LOGIN_HINT)
        return
    course_id = ctx.courses[idx]["id"]
    try:
        course, weeks = await _load_course(pool, s, ctx, course_id)
    except (InvalidCredentials, UninetUnavailable) as exc:
        await message.answer(uninet_error(exc))
        return
    await message.answer(weeks_text(course, weeks), reply_markup=weeks_keyboard(course_id, weeks))


@router.message(Command("week"))
async def cmd_week(message: Message, command: CommandObject, pool: asyncpg.Pool) -> None:
    ctx = context(message.from_user.id)
    idx = _index(command.args)
    if ctx.course_id is None or idx is None or idx >= len(ctx.weeks):
        await message.answer("Сначала /course N, потом /week N.")
        return
    week = ctx.weeks[idx]
    if not week.get("unlocked"):
        await message.answer("Эта неделя ещё закрыта.")
        return
    await typing(message)
    s = await _session(pool, message.from_user.id)
    if s is None:
        await message.answer(LOGIN_HINT)
        return
    try:
        data, videos = await _load_week(pool, s, ctx, week["id"])
    except (InvalidCredentials, UninetUnavailable) as exc:
        await message.answer(uninet_error(exc))
        return
    await message.answer(week_text(ctx.course or {}, data, videos),
                         reply_markup=week_keyboard(ctx.course_id, week["id"], videos))


@router.message(Command("watch"))
async def cmd_watch(message: Message, command: CommandObject, pool: asyncpg.Pool) -> None:
    ctx = context(message.from_user.id)
    creds = await credentials(pool, message.from_user.id)
    if creds is None:
        await message.answer(LOGIN_HINT)
        return
    arg = (command.args or "").strip().lower()
    if arg == "all":
        if ctx.course_id is None or not ctx.weeks:
            await message.answer("Сначала /course N, потом /watch all.")
            return
        week_ids = [week["id"] for week in ctx.weeks if week.get("unlocked")]
        scope = "весь курс"
    else:
        idx = _index(arg)
        if idx is None or idx >= len(ctx.weeks):
            await message.answer("Сначала /course N, потом /watch N (или /watch all).")
            return
        week = ctx.weeks[idx]
        if not week.get("unlocked"):
            await message.answer("Эта неделя ещё закрыта.")
            return
        week_ids = [week["id"]]
        scope = f"неделю {week.get('weekNumber', '?')}"
    if not week_ids:
        await message.answer("Открытых недель нет.")
        return
    queued = await _enqueue_watch(pool, creds["id"], week_ids)
    if queued == 0:
        await message.answer("Уже в очереди или смотрится.")
        return
    await message.answer(
        f"✅ Поставил в очередь {scope} ({queued}).\n"
        "uninet засчитывает только реальное время, так что это займёт столько же, "
        "сколько сами лекции. Пришлю сообщение, когда закончу."
    )


@router.message(Command("transcribe"))
async def cmd_transcribe(message: Message, command: CommandObject, pool: asyncpg.Pool) -> None:
    ctx = context(message.from_user.id)
    idx = _index(command.args)
    if idx is None or idx >= len(ctx.videos):
        await message.answer("Сначала /week N, потом /transcribe N.")
        return
    kid = _kid(ctx.videos[idx])
    if kid is None:
        await message.answer("У этого видео нет id Kinescope — транскриб недоступен.")
        return
    row = await user_row(pool, message.from_user)
    await send_transcript(message, pool, row, kid)


# --- Кнопки ------------------------------------------------------------------------


@router.callback_query(F.data == "cl")
async def cb_courses(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    await typing(query)
    s = await _session(pool, query.from_user.id)
    if s is None:
        await query.answer(LOGIN_HINT, show_alert=True)
        return
    try:
        courses = await s.courses()
    except (InvalidCredentials, UninetUnavailable) as exc:
        await query.answer(uninet_error(exc), show_alert=True)
        return
    await catalog.cache_courses(pool, courses)
    ctx = context(query.from_user.id)
    ctx.courses, ctx.course_id, ctx.course = courses, None, None
    ctx.weeks, ctx.week_id, ctx.week, ctx.videos = [], None, None, []
    await edit(query, courses_text(courses), courses_keyboard(courses))


@router.callback_query(F.data.startswith("c:"))
async def cb_course(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    course_id = int(query.data[2:])
    await typing(query)
    s = await _session(pool, query.from_user.id)
    if s is None:
        await query.answer(LOGIN_HINT, show_alert=True)
        return
    ctx = context(query.from_user.id)
    try:
        course, weeks = await _load_course(pool, s, ctx, course_id)
    except (InvalidCredentials, UninetUnavailable) as exc:
        await query.answer(uninet_error(exc), show_alert=True)
        return
    await edit(query, weeks_text(course, weeks), weeks_keyboard(course_id, weeks))


@router.callback_query(F.data.startswith("w:"))
async def cb_week(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    _, raw_course, raw_week = query.data.split(":")
    course_id, week_id = int(raw_course), int(raw_week)
    await typing(query)
    s = await _session(pool, query.from_user.id)
    if s is None:
        await query.answer(LOGIN_HINT, show_alert=True)
        return
    ctx = context(query.from_user.id)
    try:
        course = await _ensure_course(pool, s, ctx, course_id)
        week, videos = await _load_week(pool, s, ctx, week_id)
    except (InvalidCredentials, UninetUnavailable) as exc:
        await query.answer(uninet_error(exc), show_alert=True)
        return
    await edit(query, week_text(course, week, videos), week_keyboard(course_id, week_id, videos))


@router.callback_query(F.data.startswith("v:"))
async def cb_video(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    _, raw_course, raw_week, kid = query.data.split(":", 3)
    course_id, week_id = int(raw_course), int(raw_week)
    await typing(query)
    s = await _session(pool, query.from_user.id)
    if s is None:
        await query.answer(LOGIN_HINT, show_alert=True)
        return
    ctx = context(query.from_user.id)
    try:
        await _ensure_course(pool, s, ctx, course_id)
        _, videos = await _load_week(pool, s, ctx, week_id)
    except (InvalidCredentials, UninetUnavailable) as exc:
        await query.answer(uninet_error(exc), show_alert=True)
        return
    video = next((v for v in videos if _kid(v) == kid), None)
    if video is None:
        await query.answer("Видео не нашлось — открой неделю заново.", show_alert=True)
        return
    await edit(query, video_text(video), video_keyboard(course_id, week_id, kid))


@router.callback_query(F.data.startswith("t:"))
async def cb_transcript(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    await query.answer()
    row = await user_row(pool, query.from_user)
    await send_transcript(query.message, pool, row, query.data[2:])


@router.callback_query(F.data.startswith("wa:"))
async def cb_watch_week(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    creds = await credentials(pool, query.from_user.id)
    if creds is None:
        await query.answer(LOGIN_HINT, show_alert=True)
        return
    queued = await _enqueue_watch(pool, creds["id"], [int(query.data[3:])])
    await query.answer("✅ Поставил в очередь" if queued else "Уже в очереди")


@router.callback_query(F.data.startswith("wc:"))
async def cb_watch_course(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    course_id = int(query.data[3:])
    creds = await credentials(pool, query.from_user.id)
    if creds is None:
        await query.answer(LOGIN_HINT, show_alert=True)
        return
    await typing(query)
    s = await _session(pool, query.from_user.id)
    try:
        weeks = (await s.course(course_id))["weeks"] if s else []
    except (InvalidCredentials, UninetUnavailable) as exc:
        await query.answer(uninet_error(exc), show_alert=True)
        return
    week_ids = [week["id"] for week in weeks if week.get("unlocked")]
    queued = await _enqueue_watch(pool, creds["id"], week_ids)
    await query.answer(f"✅ Недель в очереди: {queued}" if queued else "Уже в очереди")

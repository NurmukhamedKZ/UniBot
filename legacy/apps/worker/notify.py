"""Live-progress callbacks for UniXWatcher, adapted to aiogram's ``Bot``
without a Dispatcher (see ``apps/worker/main.py`` for how ``Bot`` gets
constructed).

Verbatim from the Task 11 brief (docs/plan.md's Task 11 section / the
task-11-brief.md) -- the battle-tested pattern from the old bot: a single
live "⏳ video 43%" message gets edited in place instead of spammed as a new
message per progress tick.
"""

import logging

logger = logging.getLogger(__name__)


def make_callbacks(bot, chat_id: int):
    """log/edit/reset callbacks для UniXWatcher поверх Bot API."""
    video_msg_id: list[int | None] = [None]

    async def log_callback(text: str) -> None:
        await bot.send_message(chat_id, text, parse_mode="HTML")

    async def edit_callback(text: str) -> None:
        if video_msg_id[0] is None:
            sent = await bot.send_message(chat_id, text, parse_mode="HTML")
            video_msg_id[0] = sent.message_id
        else:
            try:
                await bot.edit_message_text(
                    text, chat_id=chat_id, message_id=video_msg_id[0], parse_mode="HTML"
                )
            except Exception as e:
                if "message is not modified" not in str(e).lower():
                    logger.warning("edit_callback failed: %s", e)

    async def reset_video_callback() -> None:
        video_msg_id[0] = None

    return log_callback, edit_callback, reset_video_callback

"""Central env-var config for UniBot (api, bot, worker processes).

All values are read once at import time via ``os.environ`` (with
``python-dotenv`` loading a local ``.env`` file first, if present — mirrors
the pattern used by ``packages/db/migrate.py``). Matches ``.env.example``.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BOT_USERNAME = os.environ.get("BOT_USERNAME")
WEBAPP_URL = os.environ.get("WEBAPP_URL")

DATABASE_URL = os.environ.get("DATABASE_URL")
REDIS_URL = os.environ.get("REDIS_URL")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

UNIX_EMAIL = os.environ.get("UNIX_EMAIL")
UNIX_PASSWORD = os.environ.get("UNIX_PASSWORD")

ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")

ADMIN_ID = os.environ.get("ADMIN_ID")

TRIBUTE_API_KEY = os.environ.get("TRIBUTE_API_KEY")
TRIBUTE_WEBHOOK_SECRET = os.environ.get("TRIBUTE_WEBHOOK_SECRET")


# Per-video цена падает 75 → 70 → 45 ₸. M — decoy (почти как S за видео),
# разрыв M→L всего 1700 ₸ за 2.5x больше видео — толкает к L.
PACKAGES = {
    "S": {"credits": 8, "price_kzt": 600, "stars": 100},
    "M": {"credits": 40, "price_kzt": 2800, "stars": 470},
    "L": {"credits": 100, "price_kzt": 4500, "stars": 750},
}

WELCOME_CREDITS = 2  # бонус новому юзеру
REFERRAL_CREDITS = 3  # бонус рефереру

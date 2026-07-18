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


# Сетка «как у провайдера»: per-video цена падает 149 → 50 → 30 ₸,
# разрыв M→L всего 500 ₸ — толкает к L. S = decoy.
PACKAGES = {
    "S": {"credits": 10, "price_kzt": 1490, "stars": 250},
    "M": {"credits": 50, "price_kzt": 2490, "stars": 400},
    "L": {"credits": 100, "price_kzt": 2990, "stars": 500},
}

WELCOME_CREDITS = 2  # бонус новому юзеру
REFERRAL_CREDITS = 3  # бонус рефереру

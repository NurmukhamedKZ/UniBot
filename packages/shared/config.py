"""Central env-var config for UniBot (api, bot, worker processes).

All values are read once at import time via ``os.environ`` (with
``python-dotenv`` loading a local ``.env`` file first, if present). Matches ``.env.example``.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL")

DATABASE_URL = os.environ.get("DATABASE_URL")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")

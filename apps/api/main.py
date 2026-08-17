"""FastAPI app skeleton for UniBot.

Lifespan creates the process-wide asyncpg pool and runs migrations on startup,
closes the pool on shutdown. No routers yet — catalog/watch/me/payments land in
Tasks 8-10; this task only wires up the skeleton + the ``current_user`` auth
dependency (apps/api/auth.py) so later routers can depend on it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routers.catalog import router as catalog_router
from apps.api.routers.me import router as me_router
from apps.api.routers.payments import router as payments_router
from apps.api.routers.watch import router as watch_router
from packages.db.migrate import run_migrations
from packages.db.pool import create_pool
from packages.shared.config import WEBAPP_URL


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await create_pool()
    await run_migrations(pool)
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="UniBot API", lifespan=lifespan)

# Webapp живёт на Vercel (отдельный origin) — нужен явный CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[WEBAPP_URL] if WEBAPP_URL else [],
    allow_origin_regex=r"https://.*\.vercel\.app",  # preview-деплои
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalog_router)
app.include_router(watch_router)
app.include_router(me_router)
app.include_router(payments_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

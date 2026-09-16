"""FastAPI app for UniBot.

Lifespan creates the process-wide asyncpg pool and runs migrations on startup,
closes the pool on shutdown.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routers.me import router as me_router
from apps.api.routers.uninet import NoCredentials, router as uninet_router
from packages.core.uninet import InvalidCredentials, UninetUnavailable
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

app.include_router(uninet_router)
app.include_router(me_router)


@app.exception_handler(NoCredentials)
async def _no_credentials(request: Request, exc: NoCredentials):
    return JSONResponse(status_code=409, content={"error": "no_credentials"})


@app.exception_handler(InvalidCredentials)
async def _bad_credentials(request: Request, exc: InvalidCredentials):
    return JSONResponse(status_code=409, content={"error": "bad_credentials"})


@app.exception_handler(UninetUnavailable)
async def _uninet_unavailable(request: Request, exc: UninetUnavailable):
    logging.getLogger(__name__).warning("uninet unavailable: %s", exc)
    return JSONResponse(status_code=502, content={"error": "uninet_unavailable"})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

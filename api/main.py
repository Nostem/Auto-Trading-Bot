"""
FastAPI application — REST backend for the Kalshi Bot PWA dashboard.
All routes require Bearer token authentication.
"""
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.database import engine
from api.models import Base
from api.routes import controls, dashboard, positions, reflections, trades

load_dotenv()

logger = logging.getLogger(__name__)

API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "")

app = FastAPI(
    title="Kalshi Bot API",
    description="REST backend for the Kalshi prediction market trading bot",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# CORS — open for PWA
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    # Skip auth for health check and CORS preflight
    if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)

    if not API_BEARER_TOKEN:
        logger.warning("API_BEARER_TOKEN not set — auth disabled")
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing or invalid Authorization header"},
        )

    token = auth_header.removeprefix("Bearer ").strip()
    if token != API_BEARER_TOKEN:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid token"},
        )

    return await call_next(request)

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    """Create tables if they don't exist yet (mirrors schema.sql). Does not fail startup if DB is unreachable."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Kalshi Bot API started — database connected")
    except Exception as exc:
        logger.warning(
            "Kalshi Bot API started but database connection failed (fix DATABASE_URL and restart): %s",
            exc,
        )

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(dashboard.router)
app.include_router(trades.router)
app.include_router(positions.router)
app.include_router(reflections.router)
app.include_router(controls.router)


@app.get("/health")
async def health():
    from api.database import async_session_factory
    from sqlalchemy import text
    db_ok = False
    bot_enabled = False
    try:
        async with async_session_factory() as session:
            result = await session.execute(text("SELECT value FROM settings WHERE key='bot_enabled'"))
            row = result.scalar_one_or_none()
            bot_enabled = row == "true" if row else False
            db_ok = True
    except Exception as exc:
        logger.debug("Health check DB error: %s", exc)

    return {"status": "ok", "database": db_ok, "bot_enabled": bot_enabled}

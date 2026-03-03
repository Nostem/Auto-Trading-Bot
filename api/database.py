"""
Async SQLAlchemy database setup for the FastAPI backend.
"""

import logging
import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/kalshi_bot",
)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif (
    DATABASE_URL.startswith("postgresql://")
    and "+" not in DATABASE_URL.split("://", 1)[0]
):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

scheme, sep, rest = DATABASE_URL.partition("://")
if sep and scheme.startswith("postgresql") and "+asyncpg" not in scheme:
    logger.warning("DATABASE_URL is not using asyncpg driver; forcing asyncpg")
    DATABASE_URL = f"postgresql+asyncpg://{rest}"

engine = create_async_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
    connect_args={"timeout": 30},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


_RUNTIME_MIGRATIONS = [
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS run_id VARCHAR(64)",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS session_id VARCHAR(64)",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32)",
    "ALTER TABLE positions ADD COLUMN IF NOT EXISTS run_id VARCHAR(64)",
    "ALTER TABLE positions ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32)",
    "ALTER TABLE reflections ADD COLUMN IF NOT EXISTS run_id VARCHAR(64)",
    "ALTER TABLE reflections ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32)",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS run_id VARCHAR(64)",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32)",
    "ALTER TABLE weekly_reflections ADD COLUMN IF NOT EXISTS run_id VARCHAR(64)",
    "ALTER TABLE weekly_reflections ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32)",
    "CREATE INDEX IF NOT EXISTS idx_trades_run_id ON trades(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_session_id ON trades(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_reflections_run_id ON reflections(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_recommendations_run_id ON recommendations(run_id)",
    (
        "CREATE TABLE IF NOT EXISTS bot_state ("
        "id SMALLINT PRIMARY KEY CHECK (id = 1), "
        "desired_state VARCHAR(32) NOT NULL, "
        "effective_state VARCHAR(32) NOT NULL, "
        "pause_reason VARCHAR(64), "
        "pause_detail TEXT, "
        "active_run_id VARCHAR(64) NOT NULL, "
        "session_id VARCHAR(64), "
        "last_transition_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "updated_by VARCHAR(64) NOT NULL DEFAULT 'system', "
        "version BIGINT NOT NULL DEFAULT 1"
        ")"
    ),
    (
        "CREATE TABLE IF NOT EXISTS bot_state_events ("
        "id UUID PRIMARY KEY, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "actor_type VARCHAR(32) NOT NULL, "
        "actor_id VARCHAR(128), "
        "source VARCHAR(64) NOT NULL, "
        "from_state VARCHAR(32), "
        "to_state VARCHAR(32) NOT NULL, "
        "reason VARCHAR(64), "
        "detail JSONB, "
        "run_id VARCHAR(64), "
        "session_id VARCHAR(64)"
        ")"
    ),
    "CREATE INDEX IF NOT EXISTS idx_bot_state_events_created_at ON bot_state_events(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bot_state_events_run_id ON bot_state_events(run_id)",
    "INSERT INTO settings (key, value) VALUES ('active_run_id', 'legacy') ON CONFLICT (key) DO NOTHING",
    "INSERT INTO settings (key, value) VALUES ('active_strategy_version', 'v1') ON CONFLICT (key) DO NOTHING",
    "INSERT INTO settings (key, value) VALUES ('min_expected_edge_buffer', '0.01') ON CONFLICT (key) DO NOTHING",
    "UPDATE trades SET run_id = 'legacy' WHERE run_id IS NULL",
    "UPDATE trades SET strategy_version = 'v1' WHERE strategy_version IS NULL",
    (
        "UPDATE reflections r SET run_id = COALESCE((SELECT t.run_id FROM trades t WHERE t.id = r.trade_id), 'legacy') "
        "WHERE r.run_id IS NULL"
    ),
    (
        "UPDATE reflections r SET strategy_version = COALESCE((SELECT t.strategy_version FROM trades t WHERE t.id = r.trade_id), 'v1') "
        "WHERE r.strategy_version IS NULL"
    ),
    "UPDATE recommendations SET run_id = 'legacy' WHERE run_id IS NULL",
    "UPDATE recommendations SET strategy_version = 'v1' WHERE strategy_version IS NULL",
    "UPDATE positions SET run_id = 'legacy' WHERE run_id IS NULL",
    "UPDATE positions SET strategy_version = 'v1' WHERE strategy_version IS NULL",
    "UPDATE weekly_reflections SET run_id = 'legacy' WHERE run_id IS NULL",
    "UPDATE weekly_reflections SET strategy_version = 'v1' WHERE strategy_version IS NULL",
    (
        "INSERT INTO bot_state (id, desired_state, effective_state, active_run_id, updated_by, session_id) "
        "VALUES (1, 'RUNNING', 'RUNNING', "
        "COALESCE((SELECT value FROM settings WHERE key='active_run_id' LIMIT 1), 'legacy'), "
        "'migration', "
        "'sess-legacy') "
        "ON CONFLICT (id) DO NOTHING"
    ),
]


async def apply_runtime_migrations() -> None:
    """Apply additive runtime migrations for long-lived deployments."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(42424242)"))
        for statement in _RUNTIME_MIGRATIONS:
            await conn.execute(text(statement))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

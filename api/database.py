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
    "CREATE INDEX IF NOT EXISTS idx_reflections_run_id ON reflections(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_recommendations_run_id ON recommendations(run_id)",
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

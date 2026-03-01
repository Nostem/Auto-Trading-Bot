"""
Test script for the recommendations loop.
Run with: python -m scripts.test_recommendations

Exercises:
1. Inserts sample closed trades (wins + losses) if none exist
2. Triggers generate_weekly_report() which calls generate_recommendations()
3. Queries and prints any resulting recommendations
4. Optionally tests the 3-consecutive-losses trigger
"""
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)-30s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("test_recommendations")

from api.database import async_session_factory, engine
from api.models import Base, Trade, Recommendation, Setting
from bot.intelligence.reflection_engine import ReflectionEngine
from sqlalchemy import select, func


async def ensure_schema():
    """Create tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema verified")


async def seed_sample_trades(session):
    """Insert sample trades if fewer than 5 closed trades exist."""
    result = await session.execute(
        select(func.count()).select_from(Trade).where(Trade.status == "closed")
    )
    count = result.scalar() or 0
    if count >= 5:
        logger.info("Already have %d closed trades — skipping seed", count)
        return

    logger.info("Seeding sample closed trades…")
    now = datetime.now(timezone.utc)
    samples = [
        ("bond", "yes", 0.96, 0.99, 1.5, "Bond near-certain"),
        ("btc_15min", "yes", 0.45, 0.60, 8.0, "BTC momentum play"),
        ("market_making", "yes", 0.50, 0.48, -2.5, "MM spread trade"),
        ("bond", "yes", 0.95, 0.93, -3.0, "Bond surprise drop"),
        ("btc_15min", "no", 0.40, 0.25, -6.0, "BTC wrong direction"),
        ("btc_15min", "yes", 0.55, 0.42, -7.0, "BTC volatility loss"),
        ("market_making", "yes", 0.52, 0.49, -3.5, "MM inventory risk"),
    ]
    for i, (strat, side, entry, exit_, pnl, reason) in enumerate(samples):
        trade = Trade(
            id=uuid.uuid4(),
            market_id=f"TEST-{strat.upper()}-{i:03d}",
            market_title=f"Test {strat} trade #{i}",
            strategy=strat,
            side=side,
            size=10,
            entry_price=entry,
            exit_price=exit_,
            gross_pnl=pnl + 1.4,
            fees=1.4,
            net_pnl=pnl,
            status="closed",
            entry_reasoning=reason,
            created_at=now - timedelta(days=6, hours=i),
            resolved_at=now - timedelta(days=5, hours=i),
        )
        session.add(trade)
    await session.commit()
    logger.info("Seeded %d sample trades", len(samples))


async def test_weekly_report():
    """Test the weekly report → recommendations pipeline."""
    re = ReflectionEngine()

    async with async_session_factory() as session:
        await seed_sample_trades(session)

    async with async_session_factory() as session:
        logger.info("--- Generating weekly report (will trigger recommendations) ---")
        await re.generate_weekly_report(session)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Recommendation).order_by(Recommendation.created_at.desc()).limit(10)
        )
        recs = result.scalars().all()
        if recs:
            logger.info("=== %d recommendation(s) generated ===", len(recs))
            for r in recs:
                logger.info(
                    "  [%s] %s: %s → %s (trigger=%s)\n    Reasoning: %s",
                    r.status, r.setting_key, r.current_value,
                    r.proposed_value, r.trigger, r.reasoning[:120],
                )
        else:
            logger.info("No recommendations generated (LLM may have returned empty array)")


async def test_consecutive_losses():
    """Test the 3-consecutive-losses trigger."""
    from bot.main import _check_loss_triggers

    async with async_session_factory() as session:
        # Ensure last 3 trades are all losses
        result = await session.execute(
            select(Trade)
            .where(Trade.status == "closed")
            .order_by(Trade.resolved_at.desc())
            .limit(3)
        )
        last_3 = result.scalars().all()
        all_losses = len(last_3) == 3 and all(float(t.net_pnl or 0) < 0 for t in last_3)

        if all_losses:
            logger.info("--- Last 3 trades are losses — testing consecutive loss trigger ---")
            await _check_loss_triggers(session)
        else:
            logger.info(
                "Last 3 trades are not all losses (need 3 consecutive losses to trigger). "
                "Skipping consecutive loss test."
            )


async def main():
    await ensure_schema()
    await test_weekly_report()
    await test_consecutive_losses()
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())

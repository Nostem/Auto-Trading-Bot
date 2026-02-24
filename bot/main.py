"""
Bot main entry point — wires all components together, starts APScheduler,
and runs the event loop. This is what Railway executes.
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup — must happen before any other imports that log
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)-30s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
logger = logging.getLogger("bot.main")

# ---------------------------------------------------------------------------
# Component imports (after env is loaded)
# ---------------------------------------------------------------------------

from api.database import async_session_factory, engine
from api.models import Base
from bot.core.executor import Executor
from bot.core.kalshi_client import KalshiClient
from bot.core.scanner import Scanner
from bot.intelligence.news_listener import NewsListener
from bot.intelligence.reflection_engine import ReflectionEngine
from bot.strategies.news_arbitrage import NewsArbitrageStrategy
from bot.core.risk_manager import RiskManager


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_kalshi_client: KalshiClient | None = None
_scanner = Scanner()
_executor = Executor()
_reflection_engine = ReflectionEngine()
_news_listener = NewsListener()
_news_arb_strategy = NewsArbitrageStrategy()
_risk_manager = RiskManager()
_scheduler = AsyncIOScheduler(timezone="UTC")


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def scan_and_trade():
    """Run every 60 seconds: scan for signals and execute approved ones."""
    global _kalshi_client

    async with async_session_factory() as session:
        # Re-check bot_enabled at the top of each cycle
        from sqlalchemy import select, text
        result = await session.execute(text("SELECT value FROM settings WHERE key='bot_enabled'"))
        row = result.scalar_one_or_none()
        if row != "true":
            logger.info("scan_and_trade: bot is paused — skipping cycle")
            return

        # Get current bankroll
        result = await session.execute(text("SELECT value FROM settings WHERE key='current_bankroll'"))
        bankroll_row = result.scalar_one_or_none()
        bankroll = float(bankroll_row) if bankroll_row else float(os.getenv("INITIAL_BANKROLL", "5000"))

    async with async_session_factory() as session:
        try:
            signals = await _scanner.run_scan(_kalshi_client, session, bankroll)
        except Exception as exc:
            logger.error("scan_and_trade: scanner error: %s", exc)
            return

    executed = 0
    for signal in signals:
        async with async_session_factory() as session:
            try:
                success = await _executor.execute_signal(signal, _kalshi_client, session)
                if success:
                    executed += 1
            except Exception as exc:
                logger.error("scan_and_trade: executor error on %s: %s", signal.ticker, exc)

    logger.info(
        "scan_and_trade: cycle complete — %d signal(s) found, %d executed",
        len(signals), executed,
    )


async def monitor_positions():
    """Run every 30 seconds: update unrealized PnL and check exit conditions."""
    async with async_session_factory() as session:
        try:
            await _executor.monitor_positions(
                _kalshi_client,
                session,
                reflection_callback=_reflect_on_trade,
            )
        except Exception as exc:
            logger.error("monitor_positions: error: %s", exc)


async def _reflect_on_trade(trade_dict: dict):
    """Async callback invoked after a trade closes — runs reflection non-blocking."""
    async with async_session_factory() as session:
        try:
            await _reflection_engine.reflect_on_trade(trade_dict, session)
        except Exception as exc:
            logger.error("_reflect_on_trade: error: %s", exc)


async def daily_reflection():
    """Run at 00:05 UTC daily; generate weekly report on Mondays."""
    day_of_week = datetime.now(timezone.utc).weekday()  # 0=Monday
    if day_of_week == 0:
        async with async_session_factory() as session:
            try:
                await _reflection_engine.generate_weekly_report(session)
            except Exception as exc:
                logger.error("daily_reflection: weekly report error: %s", exc)


async def news_callback(classified_headline):
    """Called by NewsListener for each relevant headline — runs news arb immediately."""
    global _kalshi_client

    async with async_session_factory() as session:
        from sqlalchemy import text
        result = await session.execute(
            text("SELECT value FROM settings WHERE key IN ('bot_enabled','news_arbitrage_enabled')")
        )
        rows = {row[0]: row[1] for row in result.fetchall()}

    if rows.get("bot_enabled") != "true" or rows.get("news_arbitrage_enabled") != "true":
        return

    try:
        signals = await _news_arb_strategy.generate_signals(classified_headline, _kalshi_client)
    except Exception as exc:
        logger.error("news_callback: strategy error: %s", exc)
        return

    for signal in signals:
        async with async_session_factory() as session:
            from sqlalchemy import text
            result = await session.execute(
                text("SELECT value FROM settings WHERE key='current_bankroll'")
            )
            bankroll_row = result.scalar_one_or_none()
            bankroll = float(bankroll_row) if bankroll_row else float(os.getenv("INITIAL_BANKROLL", "5000"))

            decision = await _risk_manager.check_trade(
                market={"ticker": signal.ticker, "volume": 10000, "yes_ask": signal.entry_price, "no_ask": signal.entry_price, "category": ""},
                side=signal.side,
                proposed_size=signal.proposed_size,
                bankroll=bankroll,
                open_positions=[],
            )
            if decision.approved:
                signal.proposed_size = decision.recommended_size
                await _executor.execute_signal(signal, _kalshi_client, session)


# ---------------------------------------------------------------------------
# Startup and shutdown
# ---------------------------------------------------------------------------

async def startup():
    """Initialize database, connect Kalshi client, start scheduler."""
    global _kalshi_client

    logger.info("Kalshi Bot starting up…")

    # Ensure DB tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema verified")

    # Open Kalshi client (stays open for the process lifetime)
    _kalshi_client = KalshiClient()
    await _kalshi_client.__aenter__()
    logger.info("Kalshi client connected")

    # Validate enabled strategies from settings
    async with async_session_factory() as session:
        from sqlalchemy import text
        result = await session.execute(text("SELECT key, value FROM settings"))
        settings = {row[0]: row[1] for row in result.fetchall()}

    active_strategies = [
        name for name, key in [
            ("Bond", "bond_strategy_enabled"),
            ("Market Making", "market_making_enabled"),
            ("News Arbitrage", "news_arbitrage_enabled"),
        ]
        if settings.get(key, "true") == "true"
    ]
    logger.info("Strategies active: %s", ", ".join(active_strategies) or "NONE")

    # Schedule jobs
    _scheduler.add_job(scan_and_trade, "interval", seconds=60, id="scan_and_trade", max_instances=1)
    _scheduler.add_job(monitor_positions, "interval", seconds=30, id="monitor_positions", max_instances=1)
    _scheduler.add_job(daily_reflection, "cron", hour=0, minute=5, id="daily_reflection")
    _scheduler.start()
    logger.info("APScheduler started")

    # Start news polling in background
    asyncio.create_task(_news_listener.start_polling(news_callback, interval_seconds=30))
    logger.info("News listener started")

    logger.info("Kalshi Bot is RUNNING")


async def shutdown():
    """Graceful shutdown — cancel MM orders and close resources."""
    global _kalshi_client

    logger.info("Kalshi Bot shutting down…")
    _news_listener.stop()
    _scheduler.shutdown(wait=False)

    if _kalshi_client:
        # Cancel all open market-making orders
        try:
            open_orders = await _kalshi_client.get_orders(status="open")
            mm_orders = [o for o in open_orders if o.get("strategy") == "market_making"]
            for order in mm_orders:
                try:
                    await _kalshi_client.cancel_order(order["order_id"])
                except Exception as exc:
                    logger.warning("Shutdown: failed to cancel order %s: %s", order.get("order_id"), exc)
            if mm_orders:
                logger.info("Shutdown: cancelled %d market making order(s)", len(mm_orders))
        except Exception as exc:
            logger.error("Shutdown: error cancelling orders: %s", exc)

        await _kalshi_client.__aexit__(None, None, None)

    logger.info("Kalshi Bot shutdown complete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    await startup()

    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()

    def _sig_handler():
        asyncio.create_task(shutdown())
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _sig_handler)

    # Run forever
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())

"""
Bot main entry point — wires all components together, starts APScheduler,
and runs the event loop. This is what Railway executes.
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone

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

from sqlalchemy import select

from api.database import async_session_factory, engine
from api.models import Base
from bot.core.executor import Executor
from bot.core.kalshi_client import KalshiClient
from bot.core.scanner import Scanner
from bot.intelligence.reflection_engine import ReflectionEngine
from bot.core.risk_manager import RiskManager


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_kalshi_client: KalshiClient | None = None
_scanner = Scanner()
_executor = Executor()
_reflection_engine = ReflectionEngine()
_risk_manager = RiskManager()
_scheduler = AsyncIOScheduler(timezone="UTC")


# ---------------------------------------------------------------------------
# LLM connectivity test
# ---------------------------------------------------------------------------

async def _test_llm_connection() -> None:
    """
    Test LLM connectivity at startup and log clear pass/fail status.
    Non-blocking — failure just means reflections use fallback text.
    """
    import anthropic as anth

    minimax_key = os.getenv("MINIMAX_CODING_PLAN_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    if minimax_key:
        provider = "MiniMax"
        base_url = "https://api.minimaxi.com/anthropic"
        client = anth.AsyncAnthropic(api_key=minimax_key, base_url=base_url)
        model = "minimax-m2.5-highspeed"
        logger.info("LLM: using MiniMax Coding Plan (base_url=%s, model=%s)", base_url, model)
    elif anthropic_key:
        provider = "Anthropic"
        client = anth.AsyncAnthropic(api_key=anthropic_key)
        model = "claude-opus-4-6"
        logger.info("LLM: using Anthropic (model=%s)", model)
    else:
        logger.warning(
            "LLM: no API key set — set MINIMAX_CODING_PLAN_API_KEY or ANTHROPIC_API_KEY. "
            "Trade reflections will use fallback text only."
        )
        return

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply with OK"}],
        )
        first_text = next(
            (b.text for b in response.content if hasattr(b, "text")), ""
        )
        logger.info("LLM: %s connected OK — response: %r", provider, first_text[:30])
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code == 401:
            if provider == "MiniMax":
                logger.warning(
                    "LLM: MiniMax returned 401 (Unauthorized). "
                    "Ensure MINIMAX_CODING_PLAN_API_KEY is a 'Coding Plan' key "
                    "from https://platform.minimaxi.com — not a pay-as-you-go key. "
                    "Reflections will use fallback text."
                )
            else:
                logger.warning(
                    "LLM: Anthropic returned 401 — check ANTHROPIC_API_KEY. "
                    "Reflections will use fallback text."
                )
        else:
            logger.warning(
                "LLM: %s connection failed (%s). "
                "Reflections will use fallback text. Error: %s",
                provider, status_code, exc,
            )


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def scan_and_trade():
    """Run every 60 seconds: scan for signals and execute approved ones."""
    global _kalshi_client

    # Hard kill switch: env var BOT_ENABLED=false always prevents trading,
    # regardless of DB setting. This is a safety override.
    if os.getenv("BOT_ENABLED", "true").lower() != "true":
        logger.debug("scan_and_trade: BOT_ENABLED env var is false — skipping cycle")
        return

    async with async_session_factory() as session:
        # Re-check bot_enabled from DB settings
        from sqlalchemy import select, text
        result = await session.execute(text("SELECT value FROM settings WHERE key='bot_enabled'"))
        row = result.scalar_one_or_none()
        if row != "true":
            logger.info("scan_and_trade: bot is paused (DB setting) — skipping cycle")
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
    """Async callback invoked after a trade closes.

    Only generates a reflection after 3 consecutive losses to reduce API calls.
    Always checks loss triggers for parameter recommendations.
    """
    is_loss = float(trade_dict.get("net_pnl", 0)) < 0

    if is_loss:
        # Check if we now have 3 consecutive losses — if so, reflect on all 3
        async with async_session_factory() as session:
            from api.models import Trade
            result = await session.execute(
                select(Trade)
                .where(Trade.status == "closed")
                .order_by(Trade.resolved_at.desc())
                .limit(3)
            )
            last_3 = result.scalars().all()
            if len(last_3) == 3 and all(float(t.net_pnl or 0) < 0 for t in last_3):
                # Reflect on the most recent trade as representative of the streak
                try:
                    await _reflection_engine.reflect_on_trade(trade_dict, session)
                    logger.info("_reflect_on_trade: 3 consecutive losses — reflection generated")
                except Exception as exc:
                    logger.error("_reflect_on_trade: error: %s", exc)

        # Check loss triggers for parameter recommendations
        async with async_session_factory() as session:
            try:
                await _check_loss_triggers(session)
            except Exception as exc:
                logger.error("_check_loss_triggers: error: %s", exc)


async def _check_loss_triggers(db_session):
    """Check for consecutive/cumulative loss triggers and generate recommendations."""
    from api.models import Trade, Recommendation
    from sqlalchemy import select, func, and_

    # --- 3 consecutive losses ---
    result = await db_session.execute(
        select(Trade)
        .where(Trade.status == "closed")
        .order_by(Trade.resolved_at.desc())
        .limit(3)
    )
    last_3 = result.scalars().all()

    if len(last_3) == 3 and all(float(t.net_pnl or 0) < 0 for t in last_3):
        # Deduplicate: check if a consecutive_losses recommendation exists since the 3rd trade resolved
        third_resolved = last_3[-1].resolved_at
        if third_resolved:
            result = await db_session.execute(
                select(Recommendation).where(
                    and_(
                        Recommendation.trigger == "consecutive_losses",
                        Recommendation.created_at >= third_resolved,
                    )
                )
            )
            if not result.scalar_one_or_none():
                logger.info("_check_loss_triggers: 3 consecutive losses detected — generating recommendations")
                await _reflection_engine.generate_recommendations(db_session, trigger="consecutive_losses")
                return  # Don't also fire cumulative in the same callback

    # --- Every 10 cumulative losses ---
    result = await db_session.execute(
        select(func.count()).select_from(Trade).where(
            and_(Trade.status == "closed", Trade.net_pnl < 0)
        )
    )
    total_losses = result.scalar() or 0

    if total_losses > 0 and total_losses % 10 == 0:
        # Check how many cumulative_losses recommendations already exist
        result = await db_session.execute(
            select(func.count()).select_from(Recommendation).where(
                Recommendation.trigger == "cumulative_losses"
            )
        )
        existing_cumulative = result.scalar() or 0
        expected = total_losses // 10

        if existing_cumulative < expected:
            logger.info(
                "_check_loss_triggers: %d cumulative losses — generating recommendations",
                total_losses,
            )
            await _reflection_engine.generate_recommendations(db_session, trigger="cumulative_losses")


async def expire_stale_recommendations():
    """Expire pending recommendations older than 7 days."""
    from api.models import Recommendation
    from sqlalchemy import select, and_

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Recommendation).where(
                and_(
                    Recommendation.status == "pending",
                    Recommendation.created_at < cutoff,
                )
            )
        )
        stale = result.scalars().all()
        if not stale:
            return

        for rec in stale:
            rec.status = "denied"
            rec.denial_reason = "Auto-expired after 7 days"
            rec.resolved_at = datetime.now(timezone.utc)

        try:
            await session.commit()
            logger.info("expire_stale_recommendations: expired %d stale recommendation(s)", len(stale))
        except Exception as exc:
            logger.error("expire_stale_recommendations: DB error: %s", exc)
            await session.rollback()


async def git_backup():
    """Auto-commit and push changes every 6 hours."""
    import subprocess

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_root,
        )
        if not result.stdout.strip():
            logger.debug("git_backup: no changes to commit")
            return

        subprocess.run(["git", "add", "-A"], cwd=project_root, check=True)
        msg = f"auto-backup {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        subprocess.run(["git", "commit", "-m", msg], cwd=project_root, check=True)
        subprocess.run(["git", "push"], cwd=project_root, check=True)
        logger.info("git_backup: committed and pushed successfully")
    except Exception as exc:
        logger.warning("git_backup: failed: %s", exc)


async def daily_reflection():
    """Run at 00:05 UTC daily; generate weekly report on Mondays."""
    day_of_week = datetime.now(timezone.utc).weekday()  # 0=Monday
    if day_of_week == 0:
        async with async_session_factory() as session:
            try:
                await _reflection_engine.generate_weekly_report(session)
            except Exception as exc:
                logger.error("daily_reflection: weekly report error: %s", exc)


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
    logger.info("Kalshi client connected (base_url=%s)", _kalshi_client.base_url)

    # Test LLM connectivity and log status
    await _test_llm_connection()

    # Validate enabled strategies from settings
    async with async_session_factory() as session:
        from sqlalchemy import text
        result = await session.execute(text("SELECT key, value FROM settings"))
        settings = {row[0]: row[1] for row in result.fetchall()}

    active_strategies = [
        name for name, key in [
            ("Bond", "bond_strategy_enabled"),
            ("Market Making", "market_making_enabled"),
            ("BTC 15-Min", "btc_strategy_enabled"),
            ("Weather", "weather_strategy_enabled"),
        ]
        if settings.get(key, "true") == "true"
    ]
    logger.info("Strategies active: %s", ", ".join(active_strategies) or "NONE")

    # Schedule jobs
    _scheduler.add_job(scan_and_trade, "interval", seconds=60, id="scan_and_trade", max_instances=1)
    _scheduler.add_job(monitor_positions, "interval", seconds=30, id="monitor_positions", max_instances=1)
    _scheduler.add_job(daily_reflection, "cron", hour=0, minute=5, id="daily_reflection")
    _scheduler.add_job(expire_stale_recommendations, "cron", hour=0, minute=10, id="expire_stale_recs")
    _scheduler.add_job(git_backup, "interval", hours=6, id="git_backup", max_instances=1)
    _scheduler.start()
    logger.info("APScheduler started")

    paper_mode = os.getenv("PAPER_TRADE", "false").lower() == "true"
    if paper_mode:
        logger.info("*** PAPER TRADE MODE — no real orders will be placed ***")
    logger.info("Kalshi Bot is RUNNING")


async def shutdown():
    """Graceful shutdown — cancel MM orders and close resources."""
    global _kalshi_client

    logger.info("Kalshi Bot shutting down…")
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

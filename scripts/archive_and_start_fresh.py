"""
Archive & Start Fresh — single command to reset everything for a new strategy run.

Usage:
    python -m scripts.archive_and_start_fresh --label paper-v4 --strategy-version v4 --enable-bot
    python -m scripts.archive_and_start_fresh --label paper-v4 --bankroll 500

This script:
1. Archives ALL open trades (cancels them)
2. Deletes ALL open positions
3. Clears reflections and recommendations tied to the current run
4. Resets bankroll to specified amount (default $1000)
5. Creates a new run_id with fresh session
6. Resets bot_state to RUNNING (or PAUSED if --no-enable-bot)
7. Sets legacy bot_enabled flag for backward compat
8. Prints summary for verification
"""

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select, text

from api.bot_state import (
    STATE_PAUSED_MANUAL,
    STATE_RUNNING,
    transition_bot_state,
)
from api.database import apply_runtime_migrations, async_session_factory
from api.models import Setting

logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")


async def _upsert_setting(session, key: str, value: str) -> None:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if setting:
        setting.value = value
        setting.updated_at = now
    else:
        session.add(Setting(key=key, value=value, updated_at=now))


async def run(args: argparse.Namespace) -> None:
    await apply_runtime_migrations()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_label = _slugify(args.label)
    run_id = f"{run_label}-{timestamp}"

    async with async_session_factory() as session:
        # --- Get prior run context ---
        result = await session.execute(
            select(Setting).where(Setting.key == "active_run_id")
        )
        prior_setting = result.scalar_one_or_none()
        prior_run_id = prior_setting.value if prior_setting else "legacy"

        # --- Step 1: Delete reflections tied to trades in current + prior run ---
        r_refl = await session.execute(text(
            "DELETE FROM reflections WHERE trade_id IN "
            f"(SELECT id FROM trades WHERE run_id IN ('{prior_run_id}', '{run_id}'))"
        ))
        print(f"Deleted {r_refl.rowcount} reflections")

        # --- Step 2: Delete recommendations ---
        r_recs = await session.execute(text(
            f"DELETE FROM recommendations WHERE run_id IN ('{prior_run_id}', '{run_id}')"
        ))
        print(f"Deleted {r_recs.rowcount} recommendations")

        # --- Step 3: Cancel all open trades ---
        now = datetime.now(timezone.utc)
        r_trades = await session.execute(text(
            f"UPDATE trades SET status='cancelled', resolved_at='{now.isoformat()}' "
            "WHERE status='open'"
        ))
        print(f"Cancelled {r_trades.rowcount} open trades")

        # --- Step 4: Delete ALL positions ---
        r_pos = await session.execute(text("DELETE FROM positions"))
        print(f"Deleted {r_pos.rowcount} positions")

        # --- Step 5: Set new run settings ---
        await _upsert_setting(session, "active_run_id", run_id)
        await _upsert_setting(session, "active_strategy_version", args.strategy_version)
        await _upsert_setting(session, "current_bankroll", f"{args.bankroll:.2f}")
        await _upsert_setting(session, "daily_loss_limit_pct", f"{args.daily_loss_limit_pct:.4f}")
        await _upsert_setting(session, "min_expected_edge_buffer", f"{args.min_edge_buffer:.4f}")
        await _upsert_setting(session, "last_run_rollover_at", now.isoformat())

        # Legacy compat
        await _upsert_setting(session, "bot_enabled", "true" if args.enable_bot else "false")

        # --- Step 6: Reset bot_state ---
        if args.enable_bot:
            await transition_bot_state(
                session,
                desired_state=STATE_RUNNING,
                effective_state=STATE_RUNNING,
                reason=None,
                detail=f"Fresh start: {run_id}",
                source="script.archive_and_start_fresh",
                actor_type="script",
                run_id=run_id,
                new_session=True,
            )
        else:
            await transition_bot_state(
                session,
                desired_state=STATE_PAUSED_MANUAL,
                effective_state=STATE_PAUSED_MANUAL,
                reason="manual_pause",
                detail=f"Fresh start (paused): {run_id}",
                source="script.archive_and_start_fresh",
                actor_type="script",
                run_id=run_id,
                new_session=False,
            )

        await session.commit()

        # --- Verification ---
        r_verify_trades = await session.execute(text(
            f"SELECT count(*) FROM trades WHERE run_id='{run_id}'"
        ))
        r_verify_pos = await session.execute(text("SELECT count(*) FROM positions"))
        r_verify_bank = await session.execute(text(
            "SELECT value FROM settings WHERE key='current_bankroll'"
        ))

        print(f"\n{'='*50}")
        print(f"  FRESH START COMPLETE")
        print(f"{'='*50}")
        print(f"  Run ID:           {run_id}")
        print(f"  Strategy Version: {args.strategy_version}")
        print(f"  Bankroll:         ${args.bankroll:.2f}")
        print(f"  Daily Loss Limit: {args.daily_loss_limit_pct:.0%}")
        print(f"  Min Edge Buffer:  {args.min_edge_buffer:.4f}")
        print(f"  Bot State:        {'RUNNING' if args.enable_bot else 'PAUSED'}")
        print(f"  Trades in Run:    {r_verify_trades.scalar()}")
        print(f"  Open Positions:   {r_verify_pos.scalar()}")
        print(f"  Bankroll (DB):    ${r_verify_bank.scalar()}")
        print(f"{'='*50}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive everything and start a completely fresh run"
    )
    parser.add_argument("--label", default="paper", help="Run label prefix")
    parser.add_argument("--strategy-version", default="v3", help="Strategy version tag")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Starting bankroll")
    parser.add_argument("--daily-loss-limit-pct", type=float, default=0.20, help="Daily loss limit %%")
    parser.add_argument("--min-edge-buffer", type=float, default=0.01, help="Min edge buffer")
    parser.add_argument("--enable-bot", action="store_true", default=True, help="Enable bot (default: yes)")
    parser.add_argument("--no-enable-bot", dest="enable_bot", action="store_false", help="Start paused")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(parse_args()))

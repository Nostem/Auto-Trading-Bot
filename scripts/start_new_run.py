"""
Start a new paper-testing run without deleting historical data.

This script:
- creates/sets active_run_id and active_strategy_version
- optionally resets bankroll and risk settings
- archives open paper state (positions + open trades)
- optionally closes pending recommendations from prior run
"""

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, select

from api.bot_state import (
    STATE_PAUSED_MANUAL,
    STATE_RUNNING,
    transition_bot_state,
)
from api.database import apply_runtime_migrations, async_session_factory
from api.models import Position, Recommendation, Setting, Trade


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")


async def _upsert_setting(db_session, key: str, value: str) -> None:
    result = await db_session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if setting:
        setting.value = value
        setting.updated_at = now
    else:
        db_session.add(Setting(key=key, value=value, updated_at=now))


async def run(args: argparse.Namespace) -> None:
    await apply_runtime_migrations()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_label = _slugify(args.label)
    run_id = f"{run_label}-{timestamp}"

    async with async_session_factory() as db_session:
        # Snapshot current run context before switching.
        result = await db_session.execute(
            select(Setting).where(Setting.key == "active_run_id")
        )
        prior_run_setting = result.scalar_one_or_none()
        prior_run_id = prior_run_setting.value if prior_run_setting else "legacy"

        # Mark open trades from the prior run as cancelled and clear positions.
        now = datetime.now(timezone.utc)
        open_trades_result = await db_session.execute(
            select(Trade).where(Trade.status == "open", Trade.run_id == prior_run_id)
        )
        open_trades = open_trades_result.scalars().all()
        for trade in open_trades:
            trade.status = "cancelled"
            trade.resolved_at = now

        positions_deleted = await db_session.execute(
            delete(Position).where(Position.run_id == prior_run_id)
        )

        if not args.keep_pending_recommendations:
            recs_result = await db_session.execute(
                select(Recommendation).where(
                    Recommendation.status == "pending",
                    Recommendation.run_id == prior_run_id,
                )
            )
            pending_recs = recs_result.scalars().all()
            for rec in pending_recs:
                rec.status = "denied"
                rec.denial_reason = "Archived due to new test run"
                rec.resolved_at = now
        else:
            pending_recs = []

        await _upsert_setting(db_session, "active_run_id", run_id)
        await _upsert_setting(
            db_session, "active_strategy_version", args.strategy_version
        )
        await _upsert_setting(
            db_session, "current_bankroll", f"{args.initial_bankroll:.2f}"
        )
        await _upsert_setting(
            db_session,
            "daily_loss_limit_pct",
            f"{args.daily_loss_limit_pct:.4f}",
        )
        await _upsert_setting(
            db_session,
            "min_expected_edge_buffer",
            f"{args.min_expected_edge_buffer:.4f}",
        )
        await _upsert_setting(
            db_session,
            "bot_enabled",
            "true" if args.enable_bot else "false",
        )
        if args.enable_bot:
            await _upsert_setting(db_session, "bot_resumed_at", now.isoformat())
        await _upsert_setting(db_session, "last_run_rollover_at", now.isoformat())

        if args.enable_bot:
            await transition_bot_state(
                db_session,
                desired_state=STATE_RUNNING,
                effective_state=STATE_RUNNING,
                reason=None,
                detail="New run started via start_new_run.py",
                source="script.start_new_run",
                actor_type="script",
                run_id=run_id,
                new_session=True,
            )
        else:
            await transition_bot_state(
                db_session,
                desired_state=STATE_PAUSED_MANUAL,
                effective_state=STATE_PAUSED_MANUAL,
                reason="manual_pause",
                detail="New run initialized in paused state",
                source="script.start_new_run",
                actor_type="script",
                run_id=run_id,
                new_session=False,
            )

        await db_session.commit()

    print("Started new run")
    print(f"run_id={run_id}")
    print(f"strategy_version={args.strategy_version}")
    print(f"archived_open_trades={len(open_trades)}")
    print(f"deleted_positions={positions_deleted.rowcount or 0}")
    print(f"archived_pending_recommendations={len(pending_recs)}")
    print(f"current_bankroll={args.initial_bankroll:.2f}")
    print(f"daily_loss_limit_pct={args.daily_loss_limit_pct:.4f}")
    print(f"min_expected_edge_buffer={args.min_expected_edge_buffer:.4f}")
    print(f"bot_enabled={'true' if args.enable_bot else 'false'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start a new strategy test run")
    parser.add_argument("--label", default="paper", help="Run label prefix")
    parser.add_argument(
        "--strategy-version",
        default="v2",
        help="Strategy version tag for new run",
    )
    parser.add_argument(
        "--initial-bankroll",
        type=float,
        default=1000.0,
        help="Reset bankroll for the new run",
    )
    parser.add_argument(
        "--daily-loss-limit-pct",
        type=float,
        default=0.20,
        help="Daily loss guardrail for paper testing",
    )
    parser.add_argument(
        "--min-expected-edge-buffer",
        type=float,
        default=0.01,
        help="Extra per-contract expected edge required above fees",
    )
    parser.add_argument(
        "--enable-bot",
        action="store_true",
        help="Enable bot immediately after rollover",
    )
    parser.add_argument(
        "--keep-pending-recommendations",
        action="store_true",
        help="Keep pending recommendations from prior run",
    )
    return parser.parse_args()


if __name__ == "__main__":
    import asyncio

    asyncio.run(run(parse_args()))

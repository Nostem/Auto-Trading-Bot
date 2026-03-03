"""Archive current run data and start a fresh run.

This script is intended for PAPER mode resets while preserving historical data
in a local JSON archive file.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from api.bot_state import STATE_RUNNING, transition_bot_state
from api.database import async_session_factory
from api.models import (
    Position,
    Recommendation,
    Reflection,
    Setting,
    Trade,
    WeeklyReflection,
)


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return (
        str(value)
        if not isinstance(value, (str, int, float, bool, type(None)))
        else value
    )


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        col.name: _serialize(getattr(row, col.name)) for col in row.__table__.columns
    }


async def _get_setting(session, key: str, default: str) -> str:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def _upsert_setting(session, key: str, value: str) -> None:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
    else:
        session.add(
            Setting(key=key, value=value, updated_at=datetime.now(timezone.utc))
        )


async def run(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")

    async with async_session_factory() as session:
        active_run_id = await _get_setting(session, "active_run_id", "legacy")
        strategy_version = await _get_setting(session, "active_strategy_version", "v1")
        current_bankroll = await _get_setting(
            session,
            "current_bankroll",
            os.getenv("INITIAL_BANKROLL", "1000"),
        )

        trades = (
            (
                await session.execute(
                    select(Trade)
                    .where(Trade.run_id == active_run_id)
                    .order_by(Trade.created_at)
                )
            )
            .scalars()
            .all()
        )
        positions = (
            (
                await session.execute(
                    select(Position).where(Position.run_id == active_run_id)
                )
            )
            .scalars()
            .all()
        )
        reflections = (
            (
                await session.execute(
                    select(Reflection)
                    .where(Reflection.run_id == active_run_id)
                    .order_by(Reflection.created_at)
                )
            )
            .scalars()
            .all()
        )
        recommendations = (
            (
                await session.execute(
                    select(Recommendation)
                    .where(Recommendation.run_id == active_run_id)
                    .order_by(Recommendation.created_at)
                )
            )
            .scalars()
            .all()
        )
        weekly = (
            (
                await session.execute(
                    select(WeeklyReflection)
                    .where(WeeklyReflection.run_id == active_run_id)
                    .order_by(WeeklyReflection.created_at)
                )
            )
            .scalars()
            .all()
        )

        archive_payload = {
            "archived_at": now.isoformat(),
            "active_run_id": active_run_id,
            "strategy_version": strategy_version,
            "current_bankroll": current_bankroll,
            "counts": {
                "trades": len(trades),
                "positions": len(positions),
                "reflections": len(reflections),
                "recommendations": len(recommendations),
                "weekly_reflections": len(weekly),
            },
            "trades": [_row_to_dict(r) for r in trades],
            "positions": [_row_to_dict(r) for r in positions],
            "reflections": [_row_to_dict(r) for r in reflections],
            "recommendations": [_row_to_dict(r) for r in recommendations],
            "weekly_reflections": [_row_to_dict(r) for r in weekly],
        }

        archive_dir = Path("archives")
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"run-archive-{active_run_id}-{stamp}.json"
        archive_path.write_text(json.dumps(archive_payload, indent=2), encoding="utf-8")

        # Clean slate for active trading state
        await session.execute(delete(Position).where(Position.run_id == active_run_id))
        await session.execute(
            delete(Trade).where(Trade.run_id == active_run_id, Trade.status == "open")
        )
        await session.execute(
            delete(Recommendation).where(
                Recommendation.run_id == active_run_id,
                Recommendation.status == "pending",
            )
        )

        new_run_id = args.run_id or f"paper-{strategy_version}-{stamp}"
        bankroll = args.bankroll or os.getenv("INITIAL_BANKROLL", "1000")

        await _upsert_setting(session, "active_run_id", new_run_id)
        await _upsert_setting(session, "active_strategy_version", strategy_version)
        await _upsert_setting(session, "current_bankroll", str(bankroll))
        await _upsert_setting(session, "last_run_rollover_at", now.isoformat())

        await transition_bot_state(
            session,
            desired_state=STATE_RUNNING,
            effective_state=STATE_RUNNING,
            reason=None,
            detail="Reset via archive_and_start_fresh.py",
            source="script.archive_and_start_fresh",
            actor_type="script",
            run_id=new_run_id,
            new_session=True,
        )

        await session.commit()

    print("Archived and reset complete")
    print(f"archive_file={archive_path}")
    print(f"old_run_id={active_run_id}")
    print(f"new_run_id={new_run_id}")
    print(f"current_bankroll={bankroll}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive active run history and start a fresh paper run",
    )
    parser.add_argument("--run-id", default="", help="Optional explicit new run ID")
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Optional bankroll override for the new run",
    )
    return parser.parse_args()


if __name__ == "__main__":
    import asyncio

    asyncio.run(run(parse_args()))

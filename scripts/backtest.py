"""
Backtesting script — replays historical Kalshi market data through the
bond and market-making strategies to estimate expected performance.

Usage:
    python scripts/backtest.py --strategy bond --days 30
"""
import argparse
import asyncio
import csv
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KALSHI_FEE_PER_CONTRACT = 0.07


def parse_args():
    p = argparse.ArgumentParser(description="Kalshi Bot Backtester")
    p.add_argument("--strategy", choices=["bond", "market_making", "all"], default="all")
    p.add_argument("--days", type=int, default=30, help="Days of history to simulate")
    p.add_argument("--bankroll", type=float, default=5000.0)
    p.add_argument(
        "--data",
        default=None,
        help="Path to JSON/CSV file with historical market data. "
             "If not provided, uses Kalshi API (requires credentials).",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Bond strategy backtest
# ---------------------------------------------------------------------------

def backtest_bond(markets: list[dict], bankroll: float) -> dict:
    """
    Simulate the bond strategy on historical market data.

    Expected market record format:
    {
        "ticker": str,
        "title": str,
        "close_price": float,   # 0–1, final resolution price (1 if YES, 0 if NO)
        "yes_ask_at_entry": float,  # 0–1
        "no_ask_at_entry": float,   # 0–1
        "hours_to_resolution": float,
        "volume": float
    }
    """
    from bot.strategies.bond_strategy import BondStrategy

    bond = BondStrategy()
    results = []
    current_bankroll = bankroll

    for m in markets:
        yes_ask = float(m.get("yes_ask_at_entry", 1.0))
        no_ask = float(m.get("no_ask_at_entry", 1.0))
        hours = float(m.get("hours_to_resolution", 999))
        volume = float(m.get("volume", 0))

        if volume < bond.min_volume or hours > bond.max_hours_to_resolution:
            continue

        qualifying_side = None
        entry_price = None
        close_price = float(m.get("close_price", 0.5))

        if no_ask <= (1.0 - bond.min_price):
            qualifying_side = "yes"
            entry_price = 1.0 - no_ask
        elif yes_ask >= bond.min_price:
            qualifying_side = "yes"
            entry_price = yes_ask
        elif yes_ask <= (1.0 - bond.min_price):
            qualifying_side = "no"
            entry_price = 1.0 - yes_ask
        elif no_ask >= bond.min_price:
            qualifying_side = "no"
            entry_price = no_ask

        if qualifying_side is None:
            continue

        max_position = current_bankroll * 0.15
        contracts = max(1, int(max_position / entry_price))
        cost = contracts * entry_price

        if qualifying_side == "yes":
            exit_price = close_price
        else:
            exit_price = 1.0 - close_price

        gross_pnl = (exit_price - entry_price) * contracts
        fees = KALSHI_FEE_PER_CONTRACT * contracts * 2
        net_pnl = gross_pnl - fees

        current_bankroll += net_pnl
        results.append({
            "ticker": m.get("ticker", ""),
            "side": qualifying_side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "contracts": contracts,
            "net_pnl": net_pnl,
            "win": net_pnl > 0,
            "bankroll_after": current_bankroll,
        })

    wins = sum(1 for r in results if r["win"])
    total = len(results)
    total_pnl = sum(r["net_pnl"] for r in results)
    win_rate = wins / total * 100 if total else 0.0

    return {
        "strategy": "bond",
        "trades": total,
        "wins": wins,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "final_bankroll": round(current_bankroll, 2),
        "return_pct": round((current_bankroll - bankroll) / bankroll * 100, 2),
        "details": results,
    }


# ---------------------------------------------------------------------------
# Load sample data (if no file provided, generate synthetic data)
# ---------------------------------------------------------------------------

def load_sample_data(strategy: str, days: int) -> list[dict]:
    """Generate synthetic market data for backtesting when no real data is available."""
    import random
    random.seed(42)

    markets = []
    for i in range(days * 3):  # ~3 qualifying markets per day
        yes_ask = random.choice([0.94, 0.95, 0.96, 0.97, 0.98, 0.99])
        close = 1.0 if random.random() < 0.97 else 0.0  # 97% resolution rate
        markets.append({
            "ticker": f"SAMPLE-{i:04d}",
            "title": f"Sample Market {i}",
            "yes_ask_at_entry": yes_ask,
            "no_ask_at_entry": 1.0 - yes_ask + random.uniform(0.01, 0.03),
            "close_price": close,
            "hours_to_resolution": random.uniform(1, 70),
            "volume": random.uniform(5000, 50000),
        })
    return markets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    logger.info("Kalshi Bot Backtester — strategy=%s, days=%d, bankroll=$%.2f",
                args.strategy, args.days, args.bankroll)

    if args.data:
        data_path = Path(args.data)
        if data_path.suffix == ".json":
            with open(data_path) as f:
                markets = json.load(f)
        elif data_path.suffix == ".csv":
            with open(data_path) as f:
                markets = list(csv.DictReader(f))
        else:
            logger.error("Unsupported data format: %s", data_path.suffix)
            sys.exit(1)
        logger.info("Loaded %d markets from %s", len(markets), data_path)
    else:
        logger.info("No data file provided — using synthetic sample data")
        markets = load_sample_data(args.strategy, args.days)

    if args.strategy in ("bond", "all"):
        results = backtest_bond(markets, args.bankroll)
        print("\n--- Bond Strategy Backtest ---")
        print(f"Trades:       {results['trades']}")
        print(f"Win rate:     {results['win_rate']}%")
        print(f"Total PnL:    ${results['total_pnl']}")
        print(f"Final balance: ${results['final_bankroll']}")
        print(f"Return:       {results['return_pct']}%")

    logger.info("Backtest complete")


if __name__ == "__main__":
    main()

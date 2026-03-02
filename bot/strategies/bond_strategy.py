"""
Bond Strategy — buy near-certain Kalshi market outcomes (priced 94¢+)
before resolution and collect the premium.
"""
import logging
import os
from datetime import datetime, timezone

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

# Black-swan discount applied to the market's implied probability
OUR_PROBABILITY = 0.97
CONFIDENCE = 0.85


class BondStrategy:
    """Scans for near-certain market outcomes to capture before resolution."""

    def __init__(self):
        self.min_price = float(os.getenv("BOND_MIN_PRICE", "0.88"))
        # Kalshi markets often resolve months/years out; allow wider window
        self.max_hours_to_resolution = float(
            os.getenv("BOND_MAX_HOURS_TO_RESOLUTION", "8760")  # 1 year default
        )
        self.min_volume = float(os.getenv("BOND_MIN_VOLUME", "5000"))

    async def scan(
        self,
        client: KalshiClient,
        open_position_tickers: set[str] | None = None,
    ) -> list[TradeSignal]:
        """
        Scan all open Kalshi markets for bond-play opportunities.

        Returns a list of TradeSignal objects sorted by expected_return_pct
        descending.
        """
        open_position_tickers = open_position_tickers or set()
        signals: list[TradeSignal] = []

        try:
            markets = await client.get_active_markets(status="open", limit=500)
        except Exception as exc:
            logger.error("BondStrategy: failed to fetch markets: %s", exc)
            return []

        logger.info("BondStrategy: scanning %d markets", len(markets))

        for market in markets:
            try:
                signal = await self._evaluate_market(client, market, open_position_tickers)
                if signal:
                    signals.append(signal)
            except Exception as exc:
                logger.warning(
                    "BondStrategy: error evaluating %s: %s",
                    market.get("ticker", "?"), exc,
                )
                continue

        signals.sort(key=lambda s: s.expected_return_pct, reverse=True)
        logger.info("BondStrategy: found %d qualifying signal(s)", len(signals))
        return signals

    async def _evaluate_market(
        self,
        client: KalshiClient,
        market: dict,
        open_position_tickers: set[str],
    ) -> TradeSignal | None:
        """Evaluate a single market for a bond opportunity."""
        ticker = market.get("ticker", "")
        if not ticker:
            return None

        # Skip markets we already hold
        if ticker in open_position_tickers:
            logger.debug("BondStrategy: skipping %s — already have position", ticker)
            return None

        # Volume filter
        volume = float(market.get("volume", 0) or 0)
        if volume < self.min_volume:
            return None

        # Time-to-resolution filter
        close_time = market.get("close_time") or market.get("expiration_time")
        if not close_time:
            return None

        hours_to_close = self._hours_until(close_time)
        if hours_to_close is None or hours_to_close > self.max_hours_to_resolution or hours_to_close <= 0:
            return None

        # Fetch orderbook to get ask prices
        try:
            orderbook = await client.get_orderbook(ticker)
        except Exception as exc:
            logger.debug("BondStrategy: orderbook fetch failed for %s: %s", ticker, exc)
            return None

        yes_ask = self._best_ask(orderbook, "yes")
        no_ask = self._best_ask(orderbook, "no")

        # Determine which side (if any) qualifies
        qualifying_side = None
        entry_price = None

        if no_ask is not None and no_ask <= (1.0 - self.min_price):
            # NO priced cheaply → YES is the near-certain side
            qualifying_side = "yes"
            entry_price = 1.0 - no_ask  # YES implied price
        elif yes_ask is not None and yes_ask >= self.min_price:
            qualifying_side = "yes"
            entry_price = yes_ask
        elif yes_ask is not None and yes_ask <= (1.0 - self.min_price):
            # YES priced cheaply → NO is the near-certain side
            qualifying_side = "no"
            entry_price = 1.0 - yes_ask
        elif no_ask is not None and no_ask >= self.min_price:
            qualifying_side = "no"
            entry_price = no_ask

        if qualifying_side is None or entry_price is None:
            return None

        expected_return_pct = (1.0 - entry_price) / entry_price
        annualized_return = expected_return_pct * (8760 / max(hours_to_close, 1))
        expected_value = OUR_PROBABILITY - entry_price

        if expected_value < 0:
            return None

        return TradeSignal(
            ticker=ticker,
            market_title=market.get("title", ticker),
            strategy="bond",
            side=qualifying_side,
            proposed_size=10,  # will be overridden by Kelly in RiskManager
            entry_price=entry_price,
            our_probability=OUR_PROBABILITY,
            expected_value=expected_value,
            expected_return_pct=expected_return_pct,
            time_to_resolution=hours_to_close,
            annualized_return=annualized_return,
            confidence=CONFIDENCE,
            reasoning=(
                f"Bond play: {qualifying_side} at {entry_price:.2f} "
                f"with {hours_to_close:.1f}h to resolution"
            ),
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _hours_until(close_time: str) -> float | None:
        """Parse an ISO 8601 timestamp and return hours from now."""
        try:
            if close_time.endswith("Z"):
                close_time = close_time[:-1] + "+00:00"
            dt = datetime.fromisoformat(close_time)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = dt - datetime.now(timezone.utc)
            return delta.total_seconds() / 3600.0
        except Exception:
            return None

    @staticmethod
    def _best_ask(orderbook: dict, side: str) -> float | None:
        """
        Extract the best (lowest) ask price for a side from the orderbook.

        Kalshi's orderbook returns {"yes": [[price, qty], ...], "no": [[price, qty], ...]}.
        The lists are resting BID orders. In a binary market:
          YES ask = (100 - best NO bid) / 100
          NO ask  = (100 - best YES bid) / 100
        """
        opposite = "no" if side == "yes" else "yes"
        levels = orderbook.get(opposite, [])
        if not levels:
            return None
        prices = []
        for level in levels:
            if isinstance(level, (list, tuple)) and len(level) >= 1:
                prices.append(int(level[0]))
            elif isinstance(level, dict):
                p = level.get("price")
                if p is not None:
                    prices.append(int(p))
        if not prices:
            return None
        best_opposite_bid = max(prices)
        return (100 - best_opposite_bid) / 100.0

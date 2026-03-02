"""
Market Making Strategy — earn the bid-ask spread by providing liquidity
on both sides of liquid Kalshi markets.
"""
import logging
import os

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

CONFIDENCE = 0.70
MM_CONTRACT_SIZE = 15  # default size per side
INVENTORY_IMBALANCE_THRESHOLD = 0.60  # cancel if one side > 60% filled


class MarketMakingStrategy:
    """Places paired limit orders on both sides of liquid markets."""

    def __init__(self):
        self.min_spread = float(os.getenv("MM_MIN_SPREAD", "0.02"))
        self.min_volume = float(os.getenv("MM_MIN_VOLUME", "5000"))
        self.min_hours_to_resolution = 4.0

    async def scan(
        self,
        client: KalshiClient,
        open_orders: list,
        open_position_tickers: set[str] | None = None,
    ) -> list[TradeSignal]:
        """
        Scan liquid markets and generate paired YES/NO limit order signals.

        Returns a flat list of TradeSignal objects (one YES + one NO per market).
        """
        open_position_tickers = open_position_tickers or set()
        signals: list[TradeSignal] = []

        # Tickers with existing MM orders — don't double-enter
        mm_tickers = {o.get("ticker", "") for o in open_orders if o.get("ticker")}

        try:
            markets = await client.get_active_markets(status="open", limit=500)
        except Exception as exc:
            logger.error("MarketMakingStrategy: failed to fetch markets: %s", exc)
            return []

        logger.info("MarketMakingStrategy: scanning %d markets", len(markets))

        for market in markets:
            try:
                new_signals = await self._evaluate_market(
                    client, market, mm_tickers, open_position_tickers
                )
                signals.extend(new_signals)
            except Exception as exc:
                logger.warning(
                    "MarketMakingStrategy: error evaluating %s: %s",
                    market.get("ticker", "?"), exc,
                )
                continue

        logger.info("MarketMakingStrategy: generated %d signal(s)", len(signals))
        return signals

    async def _evaluate_market(
        self,
        client: KalshiClient,
        market: dict,
        mm_tickers: set[str],
        open_position_tickers: set[str],
    ) -> list[TradeSignal]:
        """Return [yes_signal, no_signal] if the market qualifies, else []."""
        ticker = market.get("ticker", "")
        if not ticker:
            return []

        if ticker in mm_tickers or ticker in open_position_tickers:
            return []

        volume = float(market.get("volume", 0) or 0)
        if volume < self.min_volume:
            return []

        # Time-to-resolution filter — skip markets resolving too soon
        from bot.strategies.bond_strategy import BondStrategy
        close_time = market.get("close_time") or market.get("expiration_time")
        if not close_time:
            return []
        hours_to_close = BondStrategy._hours_until(close_time)
        if hours_to_close is None or hours_to_close < self.min_hours_to_resolution:
            return []

        try:
            orderbook = await client.get_orderbook(ticker)
        except Exception as exc:
            logger.debug("MM: orderbook fetch failed for %s: %s", ticker, exc)
            return []

        yes_bid = self._best_bid(orderbook, "yes")
        yes_ask = self._best_ask(orderbook, "yes")
        no_bid = self._best_bid(orderbook, "no")
        no_ask = self._best_ask(orderbook, "no")

        if None in (yes_bid, yes_ask, no_bid, no_ask):
            return []

        # In a binary market yes + no ≈ 1.0; spread = gap between asks
        spread = (yes_ask + no_ask) - 1.0
        if spread < self.min_spread:
            return []

        # Place our orders one penny inside the best bid on each side
        our_yes_price = round(yes_bid + 0.01, 2)
        our_no_price = round(no_bid + 0.01, 2)

        title = market.get("title", ticker)

        yes_signal = TradeSignal(
            ticker=ticker,
            market_title=title,
            strategy="market_making",
            side="yes",
            proposed_size=MM_CONTRACT_SIZE,
            entry_price=our_yes_price,
            our_probability=our_yes_price + 0.01,  # slight edge assumption
            expected_value=0.01,
            expected_return_pct=spread / 2.0,
            time_to_resolution=hours_to_close,
            annualized_return=(spread / 2.0) * (8760 / max(hours_to_close, 1)),
            confidence=CONFIDENCE,
            reasoning=(
                f"Market making: placing yes at {our_yes_price:.2f}, "
                f"spread is {spread:.3f}"
            ),
        )

        no_signal = TradeSignal(
            ticker=ticker,
            market_title=title,
            strategy="market_making",
            side="no",
            proposed_size=MM_CONTRACT_SIZE,
            entry_price=our_no_price,
            our_probability=our_no_price + 0.01,
            expected_value=0.01,
            expected_return_pct=spread / 2.0,
            time_to_resolution=hours_to_close,
            annualized_return=(spread / 2.0) * (8760 / max(hours_to_close, 1)),
            confidence=CONFIDENCE,
            reasoning=(
                f"Market making: placing no at {our_no_price:.2f}, "
                f"spread is {spread:.3f}"
            ),
        )

        return [yes_signal, no_signal]

    async def manage_inventory(
        self,
        client: KalshiClient,
        positions: list,
    ) -> list[str]:
        """
        Detect dangerously one-sided MM inventory and return order IDs to cancel.

        If one side of a paired MM position is > 60% filled without the other,
        cancel both sides to avoid directional exposure.
        """
        orders_to_cancel: list[str] = []

        # Group open orders by ticker
        try:
            open_orders = await client.get_orders(status="open")
        except Exception as exc:
            logger.error("MM inventory check: failed to fetch orders: %s", exc)
            return []

        orders_by_ticker: dict[str, list] = {}
        for order in open_orders:
            t = order.get("ticker", "")
            orders_by_ticker.setdefault(t, []).append(order)

        for ticker, orders in orders_by_ticker.items():
            mm_orders = [o for o in orders if o.get("strategy") == "market_making"]
            if len(mm_orders) < 2:
                continue

            yes_orders = [o for o in mm_orders if o.get("side") == "yes"]
            no_orders = [o for o in mm_orders if o.get("side") == "no"]

            if not yes_orders or not no_orders:
                continue

            def fill_ratio(order_list):
                total = sum(o.get("count", 0) for o in order_list)
                remaining = sum(o.get("remaining_count", o.get("count", 0)) for o in order_list)
                if total == 0:
                    return 0.0
                return (total - remaining) / total

            yes_fill = fill_ratio(yes_orders)
            no_fill = fill_ratio(no_orders)

            imbalance = abs(yes_fill - no_fill)
            if imbalance > INVENTORY_IMBALANCE_THRESHOLD:
                logger.warning(
                    "MM inventory imbalance on %s: yes_fill=%.1%%, no_fill=%.1%% — cancelling",
                    ticker, yes_fill, no_fill,
                )
                for order in mm_orders:
                    if order.get("order_id"):
                        orders_to_cancel.append(order["order_id"])

        return orders_to_cancel

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _best_bid(orderbook: dict, side: str) -> float | None:
        """
        Best bid = highest price someone will pay for this side.
        Kalshi orderbook: {"yes": [[price, qty], ...], "no": [[price, qty], ...]}.
        YES bid = max price in the `yes` list.
        NO bid  = max price in the `no` list.
        """
        levels = orderbook.get(side, [])
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
        return max(prices) / 100.0 if prices else None

    @staticmethod
    def _best_ask(orderbook: dict, side: str) -> float | None:
        """
        Best ask = cheapest price to buy this side.
        In binary markets: YES ask = (100 - best NO bid) / 100.
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

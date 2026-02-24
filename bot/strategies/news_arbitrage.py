"""
News Arbitrage Strategy — enter markets quickly after relevant news breaks,
before prices fully adjust to the new information.
"""
import logging
import re

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.news_listener import ClassifiedHeadline
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.6
ASSUMED_MISPRICING = 0.08       # 8% edge assumption on fresh news
CONFIDENCE_DISCOUNT = 0.80      # discount factor for news uncertainty
MAX_PRICE_MOVE_TO_SKIP = 0.05   # skip if price already moved > 5 cents
MIN_HOURS_TO_RESOLUTION = 2.0


class NewsArbitrageStrategy:
    """Generates trade signals triggered by classified news headlines."""

    async def generate_signals(
        self,
        classified_headline: ClassifiedHeadline,
        client: KalshiClient,
    ) -> list[TradeSignal]:
        """
        Given a classified headline, find matching open markets and generate
        trade signals if the price has not already moved.
        """
        if not classified_headline.relevant or classified_headline.confidence < MIN_CONFIDENCE:
            return []

        signals: list[TradeSignal] = []

        for category in (classified_headline.affected_categories or []):
            try:
                markets = await client.get_markets(status="open", category=category, limit=50)
            except Exception as exc:
                logger.warning("NewsArbitrage: failed to fetch %s markets: %s", category, exc)
                continue

            for market in markets:
                try:
                    signal = await self._evaluate_market(
                        market, classified_headline, client
                    )
                    if signal:
                        signals.append(signal)
                except Exception as exc:
                    logger.warning(
                        "NewsArbitrage: error on %s: %s",
                        market.get("ticker", "?"), exc,
                    )

        logger.info(
            "NewsArbitrage: '%s...' → %d signal(s)",
            classified_headline.headline[:60],
            len(signals),
        )
        return signals

    async def _evaluate_market(
        self,
        market: dict,
        headline: ClassifiedHeadline,
        client: KalshiClient,
    ) -> TradeSignal | None:
        """Evaluate whether a specific market is a good news-arb target."""
        ticker = market.get("ticker", "")
        title = market.get("title", ticker)

        if not self.keyword_match(headline.headline, title):
            return None

        # Time-to-resolution filter
        from bot.strategies.bond_strategy import BondStrategy
        close_time = market.get("close_time") or market.get("expiration_time")
        if not close_time:
            return None
        hours_to_close = BondStrategy._hours_until(close_time)
        if hours_to_close is None or hours_to_close < MIN_HOURS_TO_RESOLUTION:
            return None

        try:
            orderbook = await client.get_orderbook(ticker)
        except Exception as exc:
            logger.debug("NewsArbitrage: orderbook fetch failed for %s: %s", ticker, exc)
            return None

        # Determine trade direction
        side = "yes" if headline.direction == "yes_up" else "no"

        # Get current best ask for that side
        asks = orderbook.get(f"{side}_asks") or []
        if not asks:
            return None
        prices = [float(a.get("price", 0)) / 100.0 for a in asks if isinstance(a, dict)]
        if not prices:
            return None
        entry_price = min(prices)

        # Skip if price has already moved too much (market already priced it in)
        # We check if the ask has moved from the mid-market by more than threshold
        # Use a rough mid as 0.50 if no recent data available
        mid = float(market.get("last_price", 50)) / 100.0 if market.get("last_price") else 0.50
        price_move = abs(entry_price - mid) if side == "yes" else abs((1.0 - entry_price) - mid)
        if price_move > MAX_PRICE_MOVE_TO_SKIP:
            logger.debug(
                "NewsArbitrage: skipping %s — price already moved %.3f",
                ticker, price_move,
            )
            return None

        our_probability = entry_price + ASSUMED_MISPRICING
        expected_value = ASSUMED_MISPRICING * headline.confidence
        confidence = headline.confidence * CONFIDENCE_DISCOUNT
        expected_return_pct = (1.0 - entry_price) / entry_price if entry_price < 1.0 else 0.0
        annualized_return = expected_return_pct * (8760 / max(hours_to_close, 1))

        return TradeSignal(
            ticker=ticker,
            market_title=title,
            strategy="news_arbitrage",
            side=side,
            proposed_size=10,
            entry_price=entry_price,
            our_probability=min(our_probability, 0.99),
            expected_value=expected_value,
            expected_return_pct=expected_return_pct,
            time_to_resolution=hours_to_close,
            annualized_return=annualized_return,
            confidence=confidence,
            news_headline=headline.headline,
            reasoning=(
                f"News: '{headline.headline[:80]}' — expect {side} to move up. "
                f"{headline.reasoning}"
            ),
        )

    @staticmethod
    def keyword_match(headline: str, market_title: str) -> bool:
        """
        Return True if 2+ significant words (>4 chars) from the headline
        appear in the market title (case-insensitive).
        """
        def significant_words(text: str) -> set[str]:
            words = re.findall(r"[a-zA-Z]+", text.lower())
            return {w for w in words if len(w) > 4}

        headline_words = significant_words(headline)
        title_words = significant_words(market_title)

        overlap = headline_words & title_words
        return len(overlap) >= 2

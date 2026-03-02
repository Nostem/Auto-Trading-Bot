"""
BTC 15-Minute Strategy — scans Kalshi Bitcoin price prediction markets and
generates trade signals by comparing market-implied probability against a
fair-value estimate derived from the current BTC price and a volatility model.

Targets markets with short time horizons (15 min – 4 hours) where a lognormal
model of BTC price movement can produce a meaningful edge.
"""
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

# --- Config defaults (overridable via env) ---
_BTC_VOLATILITY_DAILY = 0.03   # 3% assumed daily vol; ~0.9% per hour (conservative)
_MIN_EDGE = 0.025              # 2.5% minimum edge for NO trades
_YES_MIN_EDGE = 0.05           # 5% minimum edge for YES trades (historically weak)
_MAX_HOURS = 8.0               # trade markets closing within 8 hours
_MIN_HOURS = 0.1               # skip markets closing in < 6 minutes
_MIN_VOLUME = 5000             # $5k minimum market volume
_CONFIDENCE = 0.60             # moderate — model is simple
_YES_MIN_ENTRY = 0.70          # YES trades must be ≥70¢ (high-probability events only)
_NO_MIN_ENTRY = 0.25           # NO trades must be ≥25¢ (fee-viable)

_COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin&vs_currencies=usd"
)


# ---------------------------------------------------------------------------
# Probability model
# ---------------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def probability_above_strike(
    current_price: float,
    strike: float,
    hours_to_resolution: float,
    daily_vol: float = _BTC_VOLATILITY_DAILY,
) -> float:
    """
    Estimate P(BTC > strike at resolution) using a lognormal model.

    Assumes zero drift (conservative; drift is negligible over short windows).
    Returns probability clipped to [0.05, 0.95] to avoid extreme certainty.
    """
    if hours_to_resolution <= 0 or current_price <= 0 or strike <= 0:
        return 0.5

    t_days = hours_to_resolution / 24.0
    sigma_t = daily_vol * math.sqrt(t_days)

    if sigma_t < 1e-9:
        return 1.0 if current_price > strike else 0.0

    # d2 = ln(S/K) / (sigma * sqrt(T)) — no drift term
    d2 = math.log(current_price / strike) / sigma_t
    prob = _normal_cdf(d2)
    return max(0.05, min(0.95, prob))


# ---------------------------------------------------------------------------
# Strike price parser
# ---------------------------------------------------------------------------

def parse_strike_from_title(title: str) -> Optional[float]:
    """
    Extract a BTC dollar strike price from a market title.

    Handles formats like:
      "Will Bitcoin be above $95,000 on March 2?"
      "BTC above $100,000?"
      "Bitcoin > $90000 at 3pm ET"
      "KXBTC-25MAR01-B90000"  ← ticker format, handled separately
    """
    # Look for $ amounts in the title
    matches = re.findall(r'\$([0-9,]+(?:\.[0-9]+)?)', title)
    for match in matches:
        try:
            price = float(match.replace(",", ""))
            if 1_000 <= price <= 2_000_000:  # sane range for BTC
                return price
        except ValueError:
            continue

    # Try to parse from ticker-style suffixes like "B90000" or "-90000"
    ticker_match = re.search(r'[B-](\d{4,7})\b', title)
    if ticker_match:
        try:
            price = float(ticker_match.group(1))
            if 1_000 <= price <= 2_000_000:
                return price
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------

class BTCStrategy:
    """
    Scans Kalshi crypto markets for Bitcoin price prediction opportunities.

    For each BTC market with a parseable strike price and suitable time horizon,
    computes P(above strike) using a lognormal model and generates a signal
    if the model edge exceeds the configured threshold.
    """

    def __init__(self):
        self.min_edge = float(os.getenv("BTC_MIN_EDGE", str(_MIN_EDGE)))
        self.max_hours = float(os.getenv("BTC_MAX_HOURS_TO_RESOLUTION", str(_MAX_HOURS)))
        self.daily_vol = float(os.getenv("BTC_DAILY_VOLATILITY", str(_BTC_VOLATILITY_DAILY)))
        self._cached_price: Optional[float] = None

    async def get_btc_price(self) -> Optional[float]:
        """
        Fetch current BTC/USD price from CoinGecko (no API key required).
        Falls back to the last cached price on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(_COINGECKO_URL)
                response.raise_for_status()
                data = response.json()
                price = float(data["bitcoin"]["usd"])
                self._cached_price = price
                logger.debug("BTCStrategy: BTC price = $%,.2f", price)
                return price
        except Exception as exc:
            logger.warning("BTCStrategy: CoinGecko fetch failed: %s", exc)
            if self._cached_price:
                logger.info("BTCStrategy: using cached price $%,.2f", self._cached_price)
            return self._cached_price

    async def scan(
        self,
        client: KalshiClient,
        open_position_tickers: set,
        db_session=None,
    ) -> list[TradeSignal]:
        """
        Main scan loop — fetch BTC price, find matching markets, score signals.
        """
        btc_price = await self.get_btc_price()
        if btc_price is None:
            logger.warning("BTCStrategy: skipping scan — no BTC price available")
            return []

        # Fetch BTC markets directly using the KXBTC series ticker.
        try:
            btc_markets = await client.get_markets(
                status="open", series_ticker="KXBTC", limit=200,
            )
        except Exception as exc:
            logger.error("BTCStrategy: failed to fetch BTC markets: %s", exc)
            return []

        if not btc_markets:
            logger.info("BTCStrategy: no open BTC markets found on Kalshi")

        # Build cooldown set: markets we traded in the last 30 minutes
        recently_traded: set[str] = set()
        if db_session:
            try:
                from api.models import Trade
                from sqlalchemy import select
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
                result = await db_session.execute(
                    select(Trade.market_id).where(
                        Trade.strategy == "btc_15min",
                        Trade.created_at >= cutoff,
                    )
                )
                recently_traded = {r[0] for r in result.fetchall()}
                if recently_traded:
                    logger.debug("BTCStrategy: %d market(s) on cooldown", len(recently_traded))
            except Exception as exc:
                logger.warning("BTCStrategy: cooldown check failed: %s", exc)

        signals: list[TradeSignal] = []
        for market in btc_markets:
            ticker = market.get("ticker", "")
            if ticker in open_position_tickers:
                continue
            if ticker in recently_traded:
                continue
            try:
                signal = self._evaluate_market(market, btc_price)
                if signal:
                    signals.append(signal)
            except Exception as exc:
                logger.warning("BTCStrategy: error on %s: %s", ticker, exc)

        logger.info(
            "BTCStrategy: scanned %d BTC markets → %d signal(s) (BTC=$%s)",
            len(btc_markets), len(signals), f"{btc_price:,.0f}",
        )
        return signals

    def _evaluate_market(
        self,
        market: dict,
        btc_price: float,
    ) -> Optional[TradeSignal]:
        """Evaluate a single market and return a TradeSignal or None."""
        from bot.strategies.bond_strategy import BondStrategy

        ticker = market.get("ticker", "")
        title = market.get("title", ticker)

        # Time filter
        close_time = market.get("close_time") or market.get("expiration_time")
        if not close_time:
            return None
        hours_to_close = BondStrategy._hours_until(close_time)
        if hours_to_close is None:
            return None
        if not (_MIN_HOURS <= hours_to_close <= self.max_hours):
            return None

        # Volume filter — BTC markets on Kalshi tend to have low volume;
        # skip only if completely dead (0 volume)
        volume = float(market.get("volume", 0) or 0)
        if volume < 1:
            return None

        # Extract strike
        strike = parse_strike_from_title(title) or parse_strike_from_title(ticker)
        if strike is None:
            return None

        # Get market's current YES ask price
        yes_ask_raw = market.get("yes_ask") or market.get("last_price")
        if yes_ask_raw is None:
            return None
        market_yes_price = float(yes_ask_raw) / 100.0

        # Fair probability from model
        our_prob_yes = probability_above_strike(
            btc_price, strike, hours_to_close, self.daily_vol
        )
        our_prob_no = 1.0 - our_prob_yes

        # Implied market probability for NO side
        market_no_price = 1.0 - market_yes_price

        # Determine best direction — YES requires higher edge (historically weak)
        yes_edge = our_prob_yes - market_yes_price
        no_edge = our_prob_no - market_no_price

        yes_min_edge = float(os.getenv("BTC_YES_MIN_EDGE", str(_YES_MIN_EDGE)))
        if no_edge >= self.min_edge and (no_edge >= yes_edge or yes_edge < yes_min_edge):
            side, entry_price, our_probability, edge = "no", market_no_price, our_prob_no, no_edge
        elif yes_edge >= yes_min_edge:
            side, entry_price, our_probability, edge = "yes", market_yes_price, our_prob_yes, yes_edge
        else:
            return None

        if entry_price >= 1.0 or entry_price <= 0.0:
            return None

        # Side-specific minimum entry prices:
        # YES trades need high-probability events (≥70¢) — cheap YES contracts lose to fees+model error
        # NO trades need ≥25¢ to be fee-viable ($0.14 round-trip fees per contract)
        min_entry = _YES_MIN_ENTRY if side == "yes" else _NO_MIN_ENTRY
        if entry_price < min_entry:
            return None

        expected_return_pct = (1.0 - entry_price) / entry_price
        annualized_return = expected_return_pct * (8760.0 / max(hours_to_close, 0.25))

        return TradeSignal(
            ticker=ticker,
            market_title=title,
            strategy="btc_15min",
            side=side,
            proposed_size=10,
            entry_price=entry_price,
            our_probability=our_probability,
            expected_value=edge,
            expected_return_pct=expected_return_pct,
            time_to_resolution=hours_to_close,
            annualized_return=annualized_return,
            confidence=_CONFIDENCE,
            reasoning=(
                f"BTC=${btc_price:,.0f} vs strike=${strike:,.0f} "
                f"({hours_to_close:.2f}h to close) — "
                f"model_prob={our_probability:.2f} market={market_yes_price:.2f} "
                f"edge={edge:.3f} side={side}"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_btc_market(market: dict) -> bool:
    """Return True if this market is a Bitcoin price prediction market."""
    ticker = market.get("ticker", "").upper()
    title = market.get("title", "").lower()
    subtitle = market.get("subtitle", "").lower()
    category = market.get("category", "").lower()
    return (
        "BTC" in ticker
        or "BITCOIN" in ticker
        or "KXBTC" in ticker
        or "bitcoin" in title
        or "bitcoin" in subtitle
        or "bitcoin" in category
        or ("btc" in title and "above" in title)
        or ("btc" in title and "below" in title)
        or ("btc" in title and "price" in title)
        or ("btc" in subtitle)
    )

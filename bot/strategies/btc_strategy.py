"""
BTC 15-Minute Strategy — RSI momentum-based approach for Kalshi Bitcoin
price prediction markets.

Uses 15-minute BTC/USDT candles from Binance to compute a 14-period RSI.
  - RSI < 35 (oversold) → buy YES on bullish contracts (expect rebound)
  - RSI > 65 (overbought) → buy NO on bullish contracts (expect pullback)

Targets open BTC markets (KXBTC series) with suitable time horizons.
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

# --- Config defaults (overridable via env) ---
_RSI_PERIOD = 14
_RSI_OVERBOUGHT = 65
_RSI_OVERSOLD = 35
_MAX_HOURS = 8.0  # trade markets closing within 8 hours
_MIN_HOURS = 0.1  # skip markets closing in < 6 minutes
_CONFIDENCE = 0.65  # moderate — RSI is a well-known indicator
_YES_MIN_ENTRY = 0.35  # YES (oversold rebound) — lower floor since RSI gives direction
_NO_MIN_ENTRY = 0.25  # NO (overbought fade) — fee-viable
_MIN_VOLUME = 0  # BTC markets on Kalshi have low volume; skip only dead ones

# Candle data endpoints (no API keys needed)
_BINANCE_US_KLINES_URL = "https://api.binance.us/api/v3/klines"
_KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

# Additional series tickers to scan beyond KXBTC
_BTC_SERIES = ["KXBTC", "KXBTCMAX", "KXBTCMIN", "KXBTCMAXY"]


# ---------------------------------------------------------------------------
# RSI calculation (manual — no talib dependency)
# ---------------------------------------------------------------------------


def calculate_rsi(closes: list[float], period: int = _RSI_PERIOD) -> Optional[float]:
    """
    Calculate the Relative Strength Index from a list of close prices.

    Uses the smoothed (Wilder's) moving average method.
    Returns None if not enough data points.
    """
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Initial average gain/loss over the first `period` deltas
    gains = [d if d > 0 else 0.0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0.0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Smoothed (Wilder's) for remaining deltas
    for d in deltas[period:]:
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


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
      "KXBTC-25MAR01-B90000"  (ticker format)
    """
    matches = re.findall(r"\$([0-9,]+(?:\.[0-9]+)?)", title)
    for match in matches:
        try:
            price = float(match.replace(",", ""))
            if 1_000 <= price <= 2_000_000:
                return price
        except ValueError:
            continue

    ticker_match = re.search(r"[B-](\d{4,7})\b", title)
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
    RSI momentum strategy for Kalshi BTC prediction markets.

    Fetches 15m BTC candles from Binance, computes RSI, and generates
    directional signals on open Kalshi BTC markets.
    """

    def __init__(self):
        self.max_hours = float(
            os.getenv("BTC_MAX_HOURS_TO_RESOLUTION", str(_MAX_HOURS))
        )
        self.rsi_overbought = float(
            os.getenv("BTC_RSI_OVERBOUGHT", str(_RSI_OVERBOUGHT))
        )
        self.rsi_oversold = float(os.getenv("BTC_RSI_OVERSOLD", str(_RSI_OVERSOLD)))
        self._cached_candles: Optional[tuple[float, list[float]]] = (
            None  # (timestamp, closes)
        )
        self._cached_price: Optional[float] = None

    async def _fetch_candles(self) -> Optional[list[float]]:
        """
        Fetch last 100 15-minute BTC/USDT candles.
        Uses Binance.US as primary, Kraken as fallback.
        Returns list of close prices. Caches for 60 seconds.
        """
        # Return cache if fresh (< 60s old)
        if self._cached_candles:
            ts, closes = self._cached_candles
            if time.time() - ts < 60:
                return closes

        closes = await self._fetch_binance_us()
        if not closes:
            closes = await self._fetch_kraken()

        if closes:
            self._cached_candles = (time.time(), closes)
            self._cached_price = closes[-1]
            logger.debug(
                "BTCStrategy: fetched %d candles, latest close=$%s",
                len(closes),
                f"{closes[-1]:,.2f}",
            )
            return closes

        logger.warning("BTCStrategy: all candle sources failed")
        if self._cached_candles:
            logger.info("BTCStrategy: using cached candles")
            return self._cached_candles[1]
        return None

    async def _fetch_binance_us(self) -> Optional[list[float]]:
        """Fetch 15m candles from Binance.US (US-accessible)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                response = await http.get(
                    _BINANCE_US_KLINES_URL,
                    params={"symbol": "BTCUSDT", "interval": "15m", "limit": 100},
                )
                response.raise_for_status()
                klines = response.json()
            closes = [float(k[4]) for k in klines]
            return closes if closes else None
        except Exception as exc:
            logger.debug("BTCStrategy: Binance.US fetch failed: %s", exc)
            return None

    async def _fetch_kraken(self) -> Optional[list[float]]:
        """Fetch 15m candles from Kraken (fallback)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                response = await http.get(
                    _KRAKEN_OHLC_URL,
                    params={"pair": "XBTUSD", "interval": 15},
                )
                response.raise_for_status()
                data = response.json()
            if data.get("error"):
                return None
            # Kraken returns {result: {XXBTZUSD: [[time,o,h,l,close,...], ...], last: ...}}
            key = [k for k in data["result"] if k != "last"][0]
            candles = data["result"][key]
            closes = [float(c[4]) for c in candles[-100:]]  # last 100
            return closes if closes else None
        except Exception as exc:
            logger.debug("BTCStrategy: Kraken fetch failed: %s", exc)
            return None

    async def scan(
        self,
        client: KalshiClient,
        open_position_tickers: set,
        db_session=None,
    ) -> list[TradeSignal]:
        """
        Main scan loop — fetch candles, compute RSI, find matching markets,
        generate signals.
        """
        closes = await self._fetch_candles()
        if closes is None:
            logger.warning("BTCStrategy: skipping scan — no candle data available")
            return []

        rsi = calculate_rsi(closes)
        if rsi is None:
            logger.warning("BTCStrategy: skipping scan — not enough data for RSI")
            return []

        btc_price = closes[-1]

        # Determine signal direction from RSI
        if rsi < self.rsi_oversold:
            rsi_side = "yes"  # oversold → expect rebound → buy YES on bullish contracts
        elif rsi > self.rsi_overbought:
            rsi_side = (
                "no"  # overbought → expect pullback → buy NO on bullish contracts
            )
        else:
            logger.info(
                "BTCStrategy: RSI=%.1f (neutral zone) — no signal (BTC=$%s)",
                rsi,
                f"{btc_price:,.0f}",
            )
            return []

        # Fetch BTC markets from multiple series tickers
        all_btc_markets: list[dict] = []
        for series in _BTC_SERIES:
            try:
                markets = await client.get_markets(
                    status="open",
                    series_ticker=series,
                    limit=200,
                )
                if markets:
                    all_btc_markets.extend(markets)
            except Exception as exc:
                logger.warning(
                    "BTCStrategy: failed to fetch %s markets: %s", series, exc
                )

        if not all_btc_markets:
            logger.info("BTCStrategy: no open BTC markets found on Kalshi")
            return []

        # Deduplicate by ticker
        seen = set()
        btc_markets = []
        for m in all_btc_markets:
            t = m.get("ticker", "")
            if t not in seen:
                seen.add(t)
                btc_markets.append(m)

        # Build cooldown set: markets we traded in the last 15 minutes
        recently_traded: set[str] = set()
        if db_session:
            try:
                from api.models import Trade
                from sqlalchemy import select

                cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
                result = await db_session.execute(
                    select(Trade.market_id).where(
                        Trade.strategy == "btc_15min",
                        Trade.created_at >= cutoff,
                    )
                )
                recently_traded = {r[0] for r in result.fetchall()}
                if recently_traded:
                    logger.debug(
                        "BTCStrategy: %d market(s) on cooldown", len(recently_traded)
                    )
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
                signal = self._evaluate_market(market, btc_price, rsi, rsi_side)
                if signal:
                    signals.append(signal)
            except Exception as exc:
                logger.warning("BTCStrategy: error on %s: %s", ticker, exc)

        logger.info(
            "BTCStrategy: RSI=%.1f (%s) BTC=$%s — scanned %d markets → %d signal(s)",
            rsi,
            rsi_side.upper(),
            f"{btc_price:,.0f}",
            len(btc_markets),
            len(signals),
        )
        return signals

    def _evaluate_market(
        self,
        market: dict,
        btc_price: float,
        rsi: float,
        rsi_side: str,
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

        # Volume filter — skip completely dead markets
        volume = float(market.get("volume", 0) or 0)
        if volume < _MIN_VOLUME:
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
        market_no_price = 1.0 - market_yes_price

        # Determine entry price based on RSI direction
        side = rsi_side
        entry_price = market_yes_price if side == "yes" else market_no_price

        if entry_price >= 1.0 or entry_price <= 0.0:
            return None

        # Side-specific minimum entry prices
        min_entry = _YES_MIN_ENTRY if side == "yes" else _NO_MIN_ENTRY
        if entry_price < min_entry:
            return None

        # RSI strength: how far into overbought/oversold territory
        if side == "yes":
            rsi_strength = (self.rsi_oversold - rsi) / self.rsi_oversold  # 0..1
        else:
            rsi_strength = (rsi - self.rsi_overbought) / (
                100 - self.rsi_overbought
            )  # 0..1
        rsi_strength = max(0.0, min(1.0, rsi_strength))

        # Edge estimate based on RSI strength and how favorable the entry price is
        # Stronger RSI signal → more confidence in directional move
        base_edge = 0.02 + (rsi_strength * 0.06)  # 2%–8% edge range
        our_probability = (
            entry_price + base_edge
            if entry_price < 0.5
            else entry_price + base_edge * 0.5
        )

        our_probability = max(0.05, min(0.95, our_probability))
        edge = our_probability - entry_price

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
                f"RSI={rsi:.1f} ({side.upper()}) BTC=${btc_price:,.0f} "
                f"strike=${strike:,.0f} ({hours_to_close:.1f}h to close) — "
                f"entry={entry_price:.2f} edge={edge:.3f} rsi_strength={rsi_strength:.2f}"
            ),
        )

"""
Weather Market Strategy — scans Kalshi temperature prediction markets and
generates trade signals by comparing market-implied probability against a
fair-value estimate derived from NOAA weather forecasts.

Weather forecasts are well-calibrated (~3-4 deg F error for 24h forecasts),
making the probability model more reliable than the BTC volatility model.
Targets markets resolving within 36 hours where forecast confidence is high.
"""
import logging
import math
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
_WEATHER_MIN_EDGE = 0.04       # 4% minimum edge
_WEATHER_MAX_HOURS = 36.0      # trade markets closing within 36 hours
_WEATHER_MIN_HOURS = 0.5       # skip markets closing in < 30 minutes
_WEATHER_CONFIDENCE = 0.70     # higher than BTC — forecasts are well-calibrated
_FORECAST_STD_24H = 3.5        # deg F forecast std dev at 24h
_FORECAST_STD_48H = 5.0        # deg F forecast std dev at 48h
_YES_MIN_ENTRY = 0.70          # YES trades must be >= 70 cents
_NO_MIN_ENTRY = 0.25           # NO trades must be >= 25 cents
_MIN_VOLUME = 5000             # $5k minimum market volume

_NOAA_USER_AGENT = "(KalshiWeatherBot, contact@example.com)"

# --- NOAA grid points for supported cities ---
# Format: (office, grid_x, grid_y)
# Look up at: https://api.weather.gov/points/{lat},{lon}
CITY_GRID_POINTS: dict[str, tuple[str, int, int]] = {
    "NYC":  ("OKX", 33, 37),
    "CHI":  ("LOT", 76, 73),
    "MIA":  ("MFL", 75, 54),
    "LA":   ("LOX", 154, 44),
    "DAL":  ("FWD", 80, 103),
    "AUS":  ("EWX", 156, 91),
    "DEN":  ("BOU", 63, 62),
    "PHIL": ("PHI", 50, 76),
    "SEA":  ("SEW", 125, 68),
    "SFO":  ("MTR", 85, 105),
    "ATL":  ("FFC", 51, 87),
    "BOS":  ("BOX", 71, 90),
    "MIN":  ("MPX", 108, 72),
    "PHX":  ("PSR", 159, 58),
    "NOLA": ("LIX", 68, 88),
}

# --- Kalshi series ticker mapping ---
# Maps series tickers to (city_key, market_type)
# market_type is "high" or "low" indicating which forecast value to use
# Original cities use KXHIGH+city, newer cities use KXHIGHT+city
# Low temp markets all use KXLOWT+city
SERIES_TICKER_MAP: dict[str, tuple[str, str]] = {
    # High temp — original cities (KXHIGH prefix)
    "KXHIGHNY":    ("NYC", "high"),
    "KXHIGHCHI":   ("CHI", "high"),
    "KXHIGHMIA":   ("MIA", "high"),
    "KXHIGHLAX":   ("LA", "high"),
    "KXHIGHAUS":   ("AUS", "high"),
    "KXHIGHDEN":   ("DEN", "high"),
    "KXHIGHPHIL":  ("PHIL", "high"),
    # High temp — newer cities (KXHIGHT prefix)
    "KXHIGHTDAL":  ("DAL", "high"),
    "KXHIGHTSEA":  ("SEA", "high"),
    "KXHIGHTSFO":  ("SFO", "high"),
    "KXHIGHTATL":  ("ATL", "high"),
    "KXHIGHTBOS":  ("BOS", "high"),
    "KXHIGHTMIN":  ("MIN", "high"),
    "KXHIGHTPHX":  ("PHX", "high"),
    "KXHIGHTNOLA": ("NOLA", "high"),
    # Low temp (KXLOWT prefix)
    "KXLOWTNYC":   ("NYC", "low"),
    "KXLOWTCHI":   ("CHI", "low"),
    "KXLOWTLAX":   ("LA", "low"),
    "KXLOWTDEN":   ("DEN", "low"),
    "KXLOWTPHIL":  ("PHIL", "low"),
    "KXLOWTAUS":   ("AUS", "low"),
    "KXLOWTMIA":   ("MIA", "low"),
}


# ---------------------------------------------------------------------------
# Probability model
# ---------------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def probability_above_threshold(
    forecast_temp: float,
    threshold: float,
    hours_to_resolution: float,
    std_24h: float = _FORECAST_STD_24H,
    std_48h: float = _FORECAST_STD_48H,
) -> float:
    """
    Estimate P(actual temp > threshold) using a normal distribution model.

    Forecast error (std dev) is interpolated linearly between 24h and 48h
    values, with a floor of 1.5 deg F. Returns probability clipped to [0.05, 0.95].
    """
    if hours_to_resolution <= 0:
        return 0.5

    # Interpolate std dev based on hours out
    if hours_to_resolution <= 24.0:
        std = std_24h * (hours_to_resolution / 24.0)
    else:
        std = std_24h + (std_48h - std_24h) * ((hours_to_resolution - 24.0) / 24.0)

    std = max(std, 1.5)  # floor

    z = (forecast_temp - threshold) / std
    prob = _normal_cdf(z)
    return max(0.05, min(0.95, prob))


# ---------------------------------------------------------------------------
# Temperature parser
# ---------------------------------------------------------------------------

def parse_temp_from_title(title: str) -> Optional[float]:
    """
    Extract a temperature threshold (deg F) from a market title.

    Handles formats like:
      "Will NYC high exceed 75 deg F?"
      "NYC high temperature above 75F"
      "Temperature above 75 degrees"
      "T75" (ticker suffix)
      "above 75"
    """
    # Pattern: ">35°" or ">35° F" — the actual Kalshi format
    angle_match = re.search(r'>\s*(\-?\d+)\s*°', title)
    if angle_match:
        try:
            temp = float(angle_match.group(1))
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            pass

    # Pattern: number followed by deg F indicator (75°F, 75 degrees F, 75F)
    matches = re.findall(r'(\-?\d+)\s*(?:°\s*F|deg(?:rees)?\s*F?|F\b)', title)
    for match in matches:
        try:
            temp = float(match)
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            continue

    # Pattern: "above/exceed/over/below/under NUMBER"
    context_match = re.search(
        r'(?:above|exceed|over|below|under|higher than|lower than)\s+(\-?\d+)',
        title, re.IGNORECASE,
    )
    if context_match:
        try:
            temp = float(context_match.group(1))
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            pass

    # Ticker-style: T followed by digits (e.g., "T75")
    ticker_match = re.search(r'\bT(\-?\d+)\b', title)
    if ticker_match:
        try:
            temp = float(ticker_match.group(1))
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# NOAA forecast fetcher
# ---------------------------------------------------------------------------

class _ForecastCache:
    """Simple per-city cache for NOAA forecasts."""

    def __init__(self):
        self._cache: dict[str, tuple[float, dict]] = {}  # city -> (timestamp, data)

    def get(self, city: str, max_age_sec: float = 7200.0) -> Optional[dict]:
        if city in self._cache:
            ts, data = self._cache[city]
            if time.time() - ts < max_age_sec:
                return data
        return None

    def set(self, city: str, data: dict) -> None:
        self._cache[city] = (time.time(), data)


_forecast_cache = _ForecastCache()


async def get_noaa_forecast(city_key: str) -> Optional[dict]:
    """
    Fetch NOAA forecast for a city grid point.

    Returns dict with keys: "high", "low", "periods" or None on failure.
    Falls back to cached data < 2h old on failure.
    """
    if city_key not in CITY_GRID_POINTS:
        return None

    office, x, y = CITY_GRID_POINTS[city_key]
    url = f"https://api.weather.gov/gridpoints/{office}/{x},{y}/forecast"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                headers={"User-Agent": _NOAA_USER_AGENT},
            )
            response.raise_for_status()
            data = response.json()

        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            logger.warning("WeatherStrategy: no forecast periods for %s", city_key)
            return _forecast_cache.get(city_key)

        # Extract high/low from first 4 periods (covers ~48h)
        highs = []
        lows = []
        for period in periods[:4]:
            temp = period.get("temperature")
            if temp is None:
                continue
            if period.get("isDaytime", True):
                highs.append(float(temp))
            else:
                lows.append(float(temp))

        result = {
            "high": highs[0] if highs else None,
            "low": lows[0] if lows else None,
            "periods": periods[:4],
        }

        _forecast_cache.set(city_key, result)
        logger.debug(
            "WeatherStrategy: NOAA forecast for %s — high=%s low=%s",
            city_key, result["high"], result["low"],
        )
        return result

    except Exception as exc:
        logger.warning("WeatherStrategy: NOAA fetch failed for %s: %s", city_key, exc)
        cached = _forecast_cache.get(city_key)
        if cached:
            logger.info("WeatherStrategy: using cached forecast for %s", city_key)
        return cached


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------

class WeatherStrategy:
    """
    Scans Kalshi weather/temperature markets for opportunities.

    For each weather market with a parseable temperature threshold and suitable
    time horizon, computes P(above threshold) using NOAA forecast data and a
    normal distribution model, generating a signal if edge exceeds threshold.
    """

    def __init__(self):
        self.min_edge = float(os.getenv("WEATHER_MIN_EDGE", str(_WEATHER_MIN_EDGE)))
        self.max_hours = float(os.getenv("WEATHER_MAX_HOURS", str(_WEATHER_MAX_HOURS)))

    async def scan(
        self,
        client: KalshiClient,
        open_position_tickers: set,
        db_session=None,
    ) -> list[TradeSignal]:
        """
        Main scan loop — fetch NOAA forecasts, find matching markets, score signals.
        """
        # Fetch forecasts for all supported cities
        forecasts: dict[str, dict] = {}
        for city_key in CITY_GRID_POINTS:
            forecast = await get_noaa_forecast(city_key)
            if forecast:
                forecasts[city_key] = forecast

        if not forecasts:
            logger.warning("WeatherStrategy: skipping scan — no forecasts available")
            return []

        # Build cooldown set: markets we traded in the last 30 minutes
        recently_traded: set[str] = set()
        if db_session:
            try:
                from api.models import Trade
                from sqlalchemy import select
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
                result = await db_session.execute(
                    select(Trade.market_id).where(
                        Trade.strategy == "weather",
                        Trade.created_at >= cutoff,
                    )
                )
                recently_traded = {r[0] for r in result.fetchall()}
                if recently_traded:
                    logger.debug("WeatherStrategy: %d market(s) on cooldown", len(recently_traded))
            except Exception as exc:
                logger.warning("WeatherStrategy: cooldown check failed: %s", exc)

        # Fetch markets for each series ticker that has a forecast
        signals: list[TradeSignal] = []
        total_markets = 0

        for series_ticker, (city_key, market_type) in SERIES_TICKER_MAP.items():
            if city_key not in forecasts:
                continue

            try:
                markets = await client.get_markets(
                    status="open", series_ticker=series_ticker, limit=200,
                )
            except Exception as exc:
                logger.warning(
                    "WeatherStrategy: failed to fetch markets for %s: %s",
                    series_ticker, exc,
                )
                continue

            if not markets:
                continue

            total_markets += len(markets)

            for market in markets:
                ticker = market.get("ticker", "")
                if ticker in open_position_tickers:
                    continue
                if ticker in recently_traded:
                    continue
                try:
                    signal = self._evaluate_market(
                        market, forecasts[city_key], market_type,
                    )
                    if signal:
                        signals.append(signal)
                except Exception as exc:
                    logger.warning("WeatherStrategy: error on %s: %s", ticker, exc)

        logger.info(
            "WeatherStrategy: scanned %d weather markets → %d signal(s)",
            total_markets, len(signals),
        )
        return signals

    def _evaluate_market(
        self,
        market: dict,
        forecast: dict,
        market_type: str,
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
        if not (_WEATHER_MIN_HOURS <= hours_to_close <= self.max_hours):
            return None

        # Volume filter
        volume = float(market.get("volume", 0) or 0)
        if volume < _MIN_VOLUME:
            return None

        # Parse temperature threshold from title
        threshold = parse_temp_from_title(title) or parse_temp_from_title(ticker)
        if threshold is None:
            return None

        # Get forecast temp (high or low depending on market type)
        forecast_temp = forecast.get(market_type)
        if forecast_temp is None:
            return None

        # Get market's current YES ask price
        yes_ask_raw = market.get("yes_ask") or market.get("last_price")
        if yes_ask_raw is None:
            return None
        market_yes_price = float(yes_ask_raw) / 100.0

        # Fair probability from model
        our_prob_yes = probability_above_threshold(
            forecast_temp, threshold, hours_to_close,
        )
        our_prob_no = 1.0 - our_prob_yes

        # Implied market probability for NO side
        market_no_price = 1.0 - market_yes_price

        # Determine best direction
        yes_edge = our_prob_yes - market_yes_price
        no_edge = our_prob_no - market_no_price

        if no_edge >= self.min_edge and (no_edge >= yes_edge or yes_edge < self.min_edge):
            side, entry_price, our_probability, edge = "no", market_no_price, our_prob_no, no_edge
        elif yes_edge >= self.min_edge:
            side, entry_price, our_probability, edge = "yes", market_yes_price, our_prob_yes, yes_edge
        else:
            return None

        if entry_price >= 1.0 or entry_price <= 0.0:
            return None

        # Side-specific minimum entry prices
        min_entry = _YES_MIN_ENTRY if side == "yes" else _NO_MIN_ENTRY
        if entry_price < min_entry:
            return None

        expected_return_pct = (1.0 - entry_price) / entry_price
        annualized_return = expected_return_pct * (8760.0 / max(hours_to_close, 0.25))

        return TradeSignal(
            ticker=ticker,
            market_title=title,
            strategy="weather",
            side=side,
            proposed_size=10,
            entry_price=entry_price,
            our_probability=our_probability,
            expected_value=edge,
            expected_return_pct=expected_return_pct,
            time_to_resolution=hours_to_close,
            annualized_return=annualized_return,
            confidence=_WEATHER_CONFIDENCE,
            reasoning=(
                f"Forecast {market_type}={forecast_temp:.0f}F vs threshold={threshold:.0f}F "
                f"({hours_to_close:.1f}h to close) — "
                f"model_prob={our_probability:.2f} market={market_yes_price:.2f} "
                f"edge={edge:.3f} side={side}"
            ),
        )

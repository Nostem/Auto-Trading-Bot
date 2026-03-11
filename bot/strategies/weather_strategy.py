"""
Weather Market Strategy — scans Kalshi temperature prediction markets and
generates trade signals by comparing market-implied probability against
Open-Meteo GFS ensemble forecasts (with NOAA fallback on API failure).
"""

import logging
import math
import os
import re
import time
from statistics import mean
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

# --- Config defaults (overridable via env) ---
_WEATHER_MIN_EDGE = 0.08  # 8% minimum edge — fees eat everything below this
_WEATHER_MAX_HOURS = 36.0  # trade markets closing within 36 hours
_WEATHER_MIN_HOURS = 0.5  # skip markets closing in < 30 minutes
_WEATHER_CONFIDENCE = 0.70  # higher than BTC — forecasts are well-calibrated
_FORECAST_STD_24H = 3.5  # deg F forecast std dev at 24h
_FORECAST_STD_48H = 5.0  # deg F forecast std dev at 48h
_YES_MIN_ENTRY = 0.25  # YES trades must be >= 25 cents
_YES_MAX_ENTRY = 0.75  # YES trades must be <= 75 cents — payoff-scaled edge required above 50¢
_NO_MIN_ENTRY = 0.25  # NO trades must be >= 25 cents
_NO_MAX_ENTRY = 0.75  # NO trades must be <= 75 cents — payoff-scaled edge required above 50¢
_MIN_VOLUME = 5000  # $5k minimum market volume
_MIN_ENSEMBLE_AGREEMENT = 0.80  # trade only when >80% of members agree
_NEAR_THRESHOLD_GUARD_F = 3.0  # skip when ensemble mean within 3°F of threshold

_NOAA_USER_AGENT = "(KalshiWeatherBot, contact@example.com)"
_OPEN_METEO_FORECAST_HOURS = 48

# --- City coordinates for supported weather markets ---
CITY_COORDS: dict[str, tuple[float, float]] = {
    "NYC": (40.7128, -74.0060),
    "CHI": (41.8781, -87.6298),
    "MIA": (25.7617, -80.1918),
    "LA": (33.9425, -118.4081),
    "DAL": (32.7767, -96.7970),
    "AUS": (30.2672, -97.7431),
    "DEN": (39.8561, -104.6737),
    "PHIL": (39.8744, -75.2424),
    "SEA": (47.4502, -122.3088),
    "SFO": (37.6213, -122.3790),
    "ATL": (33.6407, -84.4277),
    "BOS": (42.3656, -71.0096),
    "MIN": (44.8831, -93.2289),
    "PHX": (33.4373, -112.0078),
    "NOLA": (29.9926, -90.2519),
}

# --- Kalshi series ticker mapping ---
# Maps series tickers to city_key
# Original cities use KXHIGH+city, newer cities use KXHIGHT+city
# Low temp markets all use KXLOWT+city
SERIES_TICKER_MAP: dict[str, str] = {
    # High temp — original cities (KXHIGH prefix)
    "KXHIGHNY": "NYC",
    "KXHIGHCHI": "CHI",
    "KXHIGHMIA": "MIA",
    "KXHIGHLAX": "LA",
    "KXHIGHAUS": "AUS",
    "KXHIGHDEN": "DEN",
    "KXHIGHPHIL": "PHIL",
    # High temp — newer cities (KXHIGHT prefix)
    "KXHIGHTDAL": "DAL",
    "KXHIGHTSEA": "SEA",
    "KXHIGHTSFO": "SFO",
    "KXHIGHTATL": "ATL",
    "KXHIGHTBOS": "BOS",
    "KXHIGHTMIN": "MIN",
    "KXHIGHTPHX": "PHX",
    "KXHIGHTNOLA": "NOLA",
    # Low temp (KXLOWT prefix)
    "KXLOWTNYC": "NYC",
    "KXLOWTCHI": "CHI",
    "KXLOWTLAX": "LA",
    "KXLOWTDEN": "DEN",
    "KXLOWTPHIL": "PHIL",
    "KXLOWTAUS": "AUS",
    "KXLOWTMIA": "MIA",
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
    # Pattern: ">35°" / "<35°" and optional "F" suffix
    angle_match = re.search(r"[<>]\s*(\-?\d+)\s*°", title)
    if angle_match:
        try:
            temp = float(angle_match.group(1))
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            pass

    # Pattern: number followed by deg F indicator (75°F, 75 degrees F, 75F)
    matches = re.findall(r"(\-?\d+)\s*(?:°\s*F|deg(?:rees)?\s*F?|F\b)", title)
    for match in matches:
        try:
            temp = float(match)
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            continue

    # Pattern: "above/exceed/over/below/under NUMBER"
    context_match = re.search(
        r"(?:above|exceed|over|below|under|higher than|lower than)\s+(\-?\d+)",
        title,
        re.IGNORECASE,
    )
    if context_match:
        try:
            temp = float(context_match.group(1))
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            pass

    # Ticker-style: T followed by digits (e.g., "T75")
    ticker_match = re.search(r"\bT(\-?\d+)\b", title)
    if ticker_match:
        try:
            temp = float(ticker_match.group(1))
            if -30 <= temp <= 130:
                return temp
        except ValueError:
            pass

    return None


def parse_contract_direction(title: str) -> Optional[str]:
    """Return "above" or "below" based on market title wording/symbols."""
    lower = title.lower()

    if re.search(r"(<|below|under|less than|lower than|at or below)", lower):
        return "below"
    if re.search(r"(>|above|over|exceed|greater than|higher than|at or above)", lower):
        return "above"

    return None


# ---------------------------------------------------------------------------
# Forecast fetchers (Open-Meteo ensemble primary, NOAA fallback)
# ---------------------------------------------------------------------------


class _ForecastCache:
    """Simple per-city cache for weather forecasts."""

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


def _parse_iso_utc(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp into timezone-aware UTC datetime."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _c_to_f(temp_c: float) -> float:
    return (temp_c * 9.0 / 5.0) + 32.0


def _closest_index(times: list[datetime], target: datetime) -> Optional[int]:
    """Return index of forecast hour nearest to target time."""
    if not times:
        return None
    return min(
        range(len(times)), key=lambda i: abs((times[i] - target).total_seconds())
    )


async def get_open_meteo_ensemble(city_key: str) -> Optional[dict]:
    """
    Fetch Open-Meteo GFS ensemble temperatures for the next 48h.

    Returns dict with keys: source, times, members (each member is a list of F temperatures).
    """
    if city_key not in CITY_COORDS:
        return None

    cached = _forecast_cache.get(city_key)
    if cached and cached.get("source") == "open-meteo":
        return cached

    latitude, longitude = CITY_COORDS[city_key]
    url = (
        "https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={latitude}&longitude={longitude}"
        f"&hourly=temperature_2m&models=gfs_seamless&forecast_hours={_OPEN_METEO_FORECAST_HOURS}"
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        hourly = data.get("hourly", {})
        time_raw = hourly.get("time", [])
        times = [t for t in (_parse_iso_utc(v) for v in time_raw) if t is not None]
        if not times:
            logger.warning(
                "WeatherStrategy: Open-Meteo returned no times for %s", city_key
            )
            return None

        member_keys = sorted(k for k in hourly.keys() if k.startswith("temperature_2m"))
        members_f: list[list[float]] = []
        for key in member_keys:
            values = hourly.get(key)
            if not isinstance(values, list) or len(values) < len(times):
                continue
            try:
                member_f = [_c_to_f(float(v)) for v in values[: len(times)]]
            except (TypeError, ValueError):
                continue
            members_f.append(member_f)

        if len(members_f) < 2:
            logger.warning(
                "WeatherStrategy: Open-Meteo returned insufficient ensemble members for %s",
                city_key,
            )
            return None

        result = {
            "source": "open-meteo",
            "times": times,
            "members": members_f,
        }
        _forecast_cache.set(city_key, result)
        logger.debug(
            "WeatherStrategy: Open-Meteo ensemble for %s with %d members",
            city_key,
            len(members_f),
        )
        return result
    except Exception as exc:
        logger.warning(
            "WeatherStrategy: Open-Meteo fetch failed for %s: %s", city_key, exc
        )
        return None


async def get_noaa_forecast(city_key: str) -> Optional[dict]:
    """
    Fetch NOAA hourly point forecast for city coordinates.

    Returns dict with keys: source, times, temperatures_f or None on failure.
    Falls back to cached data < 2h old on failure.
    """
    if city_key not in CITY_COORDS:
        return None

    latitude, longitude = CITY_COORDS[city_key]
    points_url = f"https://api.weather.gov/points/{latitude},{longitude}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            points_response = await client.get(
                points_url,
                headers={"User-Agent": _NOAA_USER_AGENT},
            )
            points_response.raise_for_status()
            forecast_hourly_url = (
                points_response.json().get("properties", {}).get("forecastHourly")
            )
            if not forecast_hourly_url:
                logger.warning(
                    "WeatherStrategy: NOAA points missing hourly URL for %s", city_key
                )
                return _forecast_cache.get(city_key)

            hourly_response = await client.get(
                forecast_hourly_url,
                headers={"User-Agent": _NOAA_USER_AGENT},
            )
            hourly_response.raise_for_status()
            data = hourly_response.json()

        periods = data.get("properties", {}).get("periods", [])[
            :_OPEN_METEO_FORECAST_HOURS
        ]
        if not periods:
            logger.warning("WeatherStrategy: no NOAA hourly periods for %s", city_key)
            return _forecast_cache.get(city_key)

        times: list[datetime] = []
        temps_f: list[float] = []
        for period in periods:
            start = _parse_iso_utc(period.get("startTime", ""))
            temp = period.get("temperature")
            if start is None or temp is None:
                continue
            try:
                temps_f.append(float(temp))
            except (TypeError, ValueError):
                continue
            times.append(start)

        if not times or not temps_f:
            logger.warning("WeatherStrategy: NOAA hourly parse failed for %s", city_key)
            return _forecast_cache.get(city_key)

        result = {
            "source": "noaa",
            "times": times,
            "temperatures_f": temps_f,
        }

        _forecast_cache.set(city_key, result)
        logger.debug(
            "WeatherStrategy: NOAA hourly fallback loaded for %s (%d points)",
            city_key,
            len(temps_f),
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
    time horizon, computes P(above threshold) from Open-Meteo GFS ensemble
    members at the forecast hour closest to market resolution time.
    """

    def __init__(self):
        self.min_edge = float(os.getenv("WEATHER_MIN_EDGE", str(_WEATHER_MIN_EDGE)))
        self.max_hours = float(os.getenv("WEATHER_MAX_HOURS", str(_WEATHER_MAX_HOURS)))
        self.min_ensemble_agreement = float(
            os.getenv(
                "WEATHER_MIN_ENSEMBLE_AGREEMENT",
                str(_MIN_ENSEMBLE_AGREEMENT),
            )
        )
        self.near_threshold_guard_f = float(
            os.getenv(
                "WEATHER_NEAR_THRESHOLD_GUARD_F",
                str(_NEAR_THRESHOLD_GUARD_F),
            )
        )

    async def scan(
        self,
        client: KalshiClient,
        open_position_tickers: set,
        db_session=None,
    ) -> list[TradeSignal]:
        """
        Main scan loop — fetch forecasts, find matching markets, score signals.
        """
        # Fetch forecasts for all supported cities (Open-Meteo first, NOAA fallback)
        forecasts: dict[str, dict] = {}
        for city_key in CITY_COORDS:
            forecast = await get_open_meteo_ensemble(city_key)
            if not forecast:
                forecast = await get_noaa_forecast(city_key)
                if forecast:
                    logger.info(
                        "WeatherStrategy: using NOAA fallback for %s",
                        city_key,
                    )
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

                cutoff = datetime.now(timezone.utc) - timedelta(hours=6)  # 6h cooldown — don't re-enter a market we already lost on today
                result = await db_session.execute(
                    select(Trade.market_id).where(
                        Trade.strategy == "weather",
                        Trade.created_at >= cutoff,
                    )
                )
                recently_traded = {r[0] for r in result.fetchall()}
                if recently_traded:
                    logger.debug(
                        "WeatherStrategy: %d market(s) on cooldown",
                        len(recently_traded),
                    )
            except Exception as exc:
                logger.warning("WeatherStrategy: cooldown check failed: %s", exc)

        # Fetch markets for each series ticker that has a forecast
        signals: list[TradeSignal] = []
        total_markets = 0

        for series_ticker, city_key in SERIES_TICKER_MAP.items():
            if city_key not in forecasts:
                continue

            try:
                markets = await client.get_markets(
                    status="open",
                    series_ticker=series_ticker,
                    limit=200,
                )
            except Exception as exc:
                logger.warning(
                    "WeatherStrategy: failed to fetch markets for %s: %s",
                    series_ticker,
                    exc,
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
                        market,
                        forecasts[city_key],
                    )
                    if signal:
                        signals.append(signal)
                except Exception as exc:
                    logger.warning("WeatherStrategy: error on %s: %s", ticker, exc)

        logger.info(
            "WeatherStrategy: scanned %d weather markets → %d signal(s)",
            total_markets,
            len(signals),
        )
        return signals

    def _evaluate_market(
        self,
        market: dict,
        forecast: dict,
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

        close_dt = _parse_iso_utc(str(close_time))
        if close_dt is None:
            return None

        # Contract wording tells us whether YES corresponds to above/below threshold.
        contract_direction = parse_contract_direction(title)
        if contract_direction is None:
            return None

        source = forecast.get("source", "unknown")
        ensemble_agreement: Optional[float] = None
        ensemble_mean: Optional[float] = None

        if source == "open-meteo":
            forecast_times = forecast.get("times", [])
            member_series = forecast.get("members", [])

            # Determine if this is a HIGH or LOW market from the series ticker
            is_high_market = "HIGH" in ticker.upper()
            is_low_market = "LOW" in ticker.upper()

            # Find all forecast hours from now until market close
            now = datetime.now(timezone.utc)
            valid_indices = [
                i for i, t in enumerate(forecast_times)
                if now <= t <= close_dt
            ]
            if not valid_indices:
                # Fallback: use closest to close
                closest_idx = _closest_index(forecast_times, close_dt)
                if closest_idx is not None:
                    valid_indices = [closest_idx]
                else:
                    return None

            # For each ensemble member, get the relevant extreme (max for HIGH, min for LOW)
            member_temps = []
            for series in member_series:
                if not isinstance(series, list):
                    continue
                temps_in_window = [
                    float(series[i]) for i in valid_indices
                    if i < len(series)
                ]
                if not temps_in_window:
                    continue
                if is_high_market:
                    member_temps.append(max(temps_in_window))  # daily HIGH = max temp
                elif is_low_market:
                    member_temps.append(min(temps_in_window))  # daily LOW = min temp
                else:
                    # Unknown type — use closest to close
                    closest_idx = _closest_index(forecast_times, close_dt)
                    if closest_idx is not None and closest_idx < len(series):
                        member_temps.append(float(series[closest_idx]))

            if len(member_temps) < 2:
                return None

            ensemble_mean = mean(member_temps)
            if abs(ensemble_mean - threshold) < self.near_threshold_guard_f:
                return None

            above_count = sum(1 for value in member_temps if value > threshold)
            prob_above = above_count / len(member_temps)
            ensemble_agreement = max(prob_above, 1.0 - prob_above)
            if ensemble_agreement <= self.min_ensemble_agreement:
                return None
        elif source == "noaa":
            # Fallback path: use NOAA hourly point estimate + error model.
            forecast_times = forecast.get("times", [])
            temps_f = forecast.get("temperatures_f", [])
            closest_idx = _closest_index(forecast_times, close_dt)
            if closest_idx is None or closest_idx >= len(temps_f):
                return None
            ensemble_mean = float(temps_f[closest_idx])
            if abs(ensemble_mean - threshold) < self.near_threshold_guard_f:
                return None
            prob_above = probability_above_threshold(
                ensemble_mean,
                threshold,
                hours_to_close,
            )
            ensemble_agreement = max(prob_above, 1.0 - prob_above)
        else:
            return None

        # Get market's current YES ask price
        yes_ask_raw = market.get("yes_ask") or market.get("last_price")
        if yes_ask_raw is None:
            return None
        market_yes_price = float(yes_ask_raw)
        if market_yes_price > 1.0:
            market_yes_price /= 100.0
        our_prob_yes = (
            prob_above if contract_direction == "above" else (1.0 - prob_above)
        )
        our_prob_no = 1.0 - our_prob_yes

        # Implied market probability for NO side
        market_no_price = 1.0 - market_yes_price

        # Determine best direction
        yes_edge = our_prob_yes - market_yes_price
        no_edge = our_prob_no - market_no_price

        if no_edge >= self.min_edge and (
            no_edge >= yes_edge or yes_edge < self.min_edge
        ):
            side, entry_price, our_probability, edge = (
                "no",
                market_no_price,
                our_prob_no,
                no_edge,
            )
        elif yes_edge >= self.min_edge:
            side, entry_price, our_probability, edge = (
                "yes",
                market_yes_price,
                our_prob_yes,
                yes_edge,
            )
        else:
            return None

        if entry_price >= 1.0 or entry_price <= 0.0:
            return None

        # Payoff-scaled minimum edge: higher entry prices need more edge
        # to overcome asymmetric risk (losing 70¢ vs gaining 30¢ at 70¢ entry)
        # At 50¢: 8% (standard). At 65¢: ~15%. At 75¢: ~20%.
        if entry_price <= 0.50:
            required_edge = self.min_edge  # 8%
        else:
            required_edge = self.min_edge + (entry_price - 0.50) * 0.48
        if edge < required_edge:
            logger.debug(
                "WeatherStrategy: skipping %s — edge %.1f%% below payoff-scaled min %.1f%% (entry=%.2f)",
                ticker, edge * 100, required_edge * 100, entry_price,
            )
            return None

        # Side-specific entry price bounds
        min_entry = _YES_MIN_ENTRY if side == "yes" else _NO_MIN_ENTRY
        max_entry = _YES_MAX_ENTRY if side == "yes" else _NO_MAX_ENTRY
        if entry_price < min_entry or entry_price > max_entry:
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
                f"Forecast source={source} mean={ensemble_mean:.1f}F vs threshold={threshold:.0f}F "
                f"({hours_to_close:.1f}h to close) — "
                f"agreement={ensemble_agreement:.2f} "
                f"contract_yes={contract_direction} model_prob={our_probability:.2f} "
                f"market_yes={market_yes_price:.2f} "
                f"edge={edge:.3f} side={side}"
            ),
        )

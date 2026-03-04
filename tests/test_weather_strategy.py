from datetime import datetime, timedelta, timezone

from bot.strategies.weather_strategy import (
    WeatherStrategy,
    parse_contract_direction,
)


class TestParseContractDirection:
    def test_symbol_below(self):
        direction = parse_contract_direction(
            "Will the high temp in LA be <67° on Mar 3, 2026?"
        )
        assert direction == "below"

    def test_symbol_above(self):
        direction = parse_contract_direction(
            "Will the minimum temperature be >68° on Mar 4, 2026?"
        )
        assert direction == "above"


class TestWeatherDirectionality:
    def test_above_contract_prefers_yes_when_forecast_above(self):
        strategy = WeatherStrategy()
        close_time = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

        market = {
            "ticker": "KXHIGHTDAL-26MAR04-T81",
            "title": "Will the maximum temperature be >81° on Mar 4, 2026?",
            "close_time": close_time,
            "volume": 10000,
            "yes_ask": 60,
        }
        forecast = {"high": 86.0, "low": 60.0}

        signal = strategy._evaluate_market(market, forecast, "high")

        assert signal is not None
        assert signal.side == "yes"
        assert signal.expected_value > 0

    def test_below_contract_prefers_no_when_forecast_above(self):
        strategy = WeatherStrategy()
        close_time = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

        market = {
            "ticker": "KXHIGHLAX-26MAR03-T67",
            "title": "Will the high temp in LA be <67° on Mar 3, 2026?",
            "close_time": close_time,
            "volume": 10000,
            "yes_ask": 60,
        }
        forecast = {"high": 73.0, "low": 55.0}

        signal = strategy._evaluate_market(market, forecast, "high")

        assert signal is not None
        assert signal.side == "no"
        assert signal.expected_value > 0

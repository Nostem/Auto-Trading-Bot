"""
Signal scorer — ranks and filters TradeSignal objects by expected value
and risk-adjusted return before they reach the executor.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Annualized return cap used for normalization so outliers don't dominate scoring
_MAX_ANNUALIZED_RETURN = 5.0  # 500% annual cap for normalization


@dataclass
class TradeSignal:
    """Universal trade opportunity representation. Every strategy produces these."""
    ticker: str
    market_title: str
    strategy: str                    # 'bond' | 'market_making' | 'news_arbitrage'
    side: str                        # 'yes' | 'no'
    proposed_size: int
    entry_price: float               # 0.0 – 1.0
    our_probability: float           # estimated true probability
    expected_value: float            # our_prob - entry_price (edge)
    expected_return_pct: float       # (1.0 - entry_price) / entry_price if win
    time_to_resolution: float        # hours until market closes
    annualized_return: float         # expected_return_pct extrapolated to annual
    confidence: float                # 0.0 – 1.0
    reasoning: str
    news_headline: Optional[str] = None
    score: float = field(default=0.0, compare=False)


class SignalScorer:
    """Scores, ranks, and filters trade signals by composite quality metric."""

    def score_signal(self, signal: TradeSignal) -> float:
        """
        Composite score = (expected_value * 0.4) + (annualized_return_norm * 0.3) + (confidence * 0.3)
        Penalizes signals with time_to_resolution > 48h by 20%.
        """
        ev_component = signal.expected_value * 0.4

        # Normalize annualized return to [0, 1] range
        annualized_norm = min(signal.annualized_return, _MAX_ANNUALIZED_RETURN) / _MAX_ANNUALIZED_RETURN
        annualized_component = annualized_norm * 0.3

        confidence_component = signal.confidence * 0.3

        score = ev_component + annualized_component + confidence_component

        # Penalize long-dated positions
        if signal.time_to_resolution > 48:
            score *= 0.80

        logger.debug(
            "Scored %s %s: ev=%.3f ann=%.3f conf=%.3f → score=%.4f",
            signal.ticker, signal.side,
            ev_component, annualized_component, confidence_component, score,
        )
        return score

    def rank_signals(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        """
        Score every signal, deduplicate by ticker+side, and return sorted
        by score descending.
        """
        seen: set[str] = set()
        unique: list[TradeSignal] = []

        for signal in signals:
            key = f"{signal.ticker}:{signal.side}"
            if key not in seen:
                seen.add(key)
                signal.score = self.score_signal(signal)
                unique.append(signal)

        unique.sort(key=lambda s: s.score, reverse=True)
        return unique

    def filter_minimum_edge(
        self,
        signals: list[TradeSignal],
        min_edge: float = 0.02,
    ) -> list[TradeSignal]:
        """Remove signals where expected_value (edge) is below the threshold."""
        filtered = [s for s in signals if s.expected_value >= min_edge]
        removed = len(signals) - len(filtered)
        if removed:
            logger.debug("Filtered %d signal(s) below min_edge=%.3f", removed, min_edge)
        return filtered

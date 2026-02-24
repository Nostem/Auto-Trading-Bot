"""
Risk Manager — enforces all trading safety rules.
Every trade must be approved by RiskManager.check_trade() before execution.
"""
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Fees per contract per side (Kalshi charges ~$0.07/contract/side)
KALSHI_FEE_PER_CONTRACT = 0.07


@dataclass
class TradeDecision:
    approved: bool
    recommended_size: int
    reason: str
    kelly_fraction: float = 0.0


class RiskManager:
    """
    Enforces hard position limits, daily loss limits, Kelly sizing,
    correlation rules, and minimum liquidity requirements.
    """

    def __init__(self):
        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", "0.15"))
        self.daily_loss_limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.03"))
        self.max_total_exposure_pct = 0.60
        self.min_market_volume = 5000.0
        self.max_category_positions = 2
        self.category_window_hours = 48

    # -------------------------------------------------------------------------
    # Main trade approval
    # -------------------------------------------------------------------------

    async def check_trade(
        self,
        market: dict,
        side: str,
        proposed_size: int,
        bankroll: float,
        open_positions: list,
    ) -> TradeDecision:
        """
        Validate a proposed trade against all risk rules.

        Returns TradeDecision with approved=True and recommended_size if safe,
        or approved=False with reason if any rule is violated.
        """
        ticker = market.get("ticker", "UNKNOWN")

        # --- Rule 1: Minimum liquidity ---
        volume = float(market.get("volume", 0) or 0)
        if volume < self.min_market_volume:
            reason = f"Rejected {ticker}: volume ${volume:.0f} below minimum ${self.min_market_volume:.0f}"
            logger.warning(reason)
            return TradeDecision(approved=False, recommended_size=0, reason=reason)

        # --- Rule 2: Max total exposure ---
        total_exposure = self._calculate_total_exposure(open_positions, bankroll)
        if total_exposure >= self.max_total_exposure_pct:
            reason = (
                f"Rejected {ticker}: total exposure {total_exposure:.1%} "
                f"already at or above {self.max_total_exposure_pct:.0%} limit"
            )
            logger.warning(reason)
            return TradeDecision(approved=False, recommended_size=0, reason=reason)

        # --- Rule 3: Max single position size ---
        max_allowed_size = self.get_max_position_size(bankroll)
        trade_cost = proposed_size * float(market.get("yes_ask", 0.5))
        if trade_cost > max_allowed_size:
            # Clamp to max allowed rather than reject outright
            entry_price = float(market.get("yes_ask", 0.5)) if side == "yes" else float(market.get("no_ask", 0.5))
            if entry_price <= 0:
                entry_price = 0.5
            proposed_size = max(1, int(max_allowed_size / entry_price))
            logger.info(
                "Clamped %s size to %d contracts (max position $%.2f)",
                ticker, proposed_size, max_allowed_size,
            )

        # --- Rule 4: Correlation limit (same category, last 48h) ---
        category = market.get("category", "")
        recent_category_count = self._count_recent_category_positions(open_positions, category)
        if recent_category_count >= self.max_category_positions:
            reason = (
                f"Rejected {ticker}: already {recent_category_count} positions "
                f"in category '{category}' within {self.category_window_hours}h"
            )
            logger.warning(reason)
            return TradeDecision(approved=False, recommended_size=0, reason=reason)

        return TradeDecision(
            approved=True,
            recommended_size=proposed_size,
            reason="All risk checks passed",
            kelly_fraction=0.0,
        )

    # -------------------------------------------------------------------------
    # Kelly Criterion sizing
    # -------------------------------------------------------------------------

    def calculate_kelly_size(
        self,
        our_probability: float,
        market_price: float,
        bankroll: float,
    ) -> int:
        """
        Calculate optimal contract count using half-Kelly criterion.

        Kelly formula: f = (bp - q) / b
        where b = odds (1/price - 1), p = our_probability, q = 1 - p

        Uses half-Kelly for variance reduction.
        Returns number of contracts (each contract pays $1 if correct).
        """
        if market_price <= 0 or market_price >= 1:
            return 0

        b = (1.0 / market_price) - 1.0  # net odds
        p = our_probability
        q = 1.0 - p

        kelly_fraction = (b * p - q) / b
        if kelly_fraction <= 0:
            return 0

        half_kelly = kelly_fraction * 0.5
        kelly_amount = bankroll * half_kelly

        # Number of contracts = dollar amount / cost per contract
        contracts = int(kelly_amount / market_price)
        logger.debug(
            "Kelly sizing: p=%.3f, market=%.3f, b=%.3f → f=%.3f → %d contracts",
            our_probability, market_price, b, kelly_fraction, contracts,
        )
        return max(1, contracts)

    # -------------------------------------------------------------------------
    # Daily loss limit
    # -------------------------------------------------------------------------

    async def check_daily_loss_limit(self, db_session) -> bool:
        """
        Returns True if the daily loss limit has been hit (bot should pause).
        Queries today's closed trades to compute realized PnL.
        """
        from api.models import Trade, Setting

        # Get current bankroll from settings
        result = await db_session.execute(
            select(Setting).where(Setting.key == "current_bankroll")
        )
        setting = result.scalar_one_or_none()
        bankroll = float(setting.value) if setting else float(os.getenv("INITIAL_BANKROLL", "5000"))

        # Get today's resolved PnL
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = await db_session.execute(
            select(func.sum(Trade.net_pnl)).where(
                Trade.resolved_at >= today_start,
                Trade.status == "closed",
            )
        )
        today_pnl = result.scalar() or 0.0

        loss_limit = bankroll * self.daily_loss_limit_pct
        if today_pnl <= -loss_limit:
            logger.critical(
                "Daily loss limit hit: today_pnl=%.2f, limit=-%.2f (%.1%% of bankroll=%.2f)",
                today_pnl, loss_limit, self.daily_loss_limit_pct, bankroll,
            )
            return True

        remaining = loss_limit + today_pnl
        if remaining < loss_limit * 0.25:
            logger.warning(
                "Approaching daily loss limit: today_pnl=%.2f, only $%.2f remaining",
                today_pnl, remaining,
            )

        return False

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def get_max_position_size(self, bankroll: float) -> float:
        """Return max dollar amount for a single position."""
        return bankroll * min(self.max_position_pct, 0.20)

    def _calculate_total_exposure(self, open_positions: list, bankroll: float) -> float:
        """Return current total exposure as a fraction of bankroll."""
        if not open_positions or bankroll <= 0:
            return 0.0
        total_cost = sum(
            float(p.get("size", 0)) * float(p.get("entry_price", 0))
            for p in open_positions
        )
        return total_cost / bankroll

    def _count_recent_category_positions(
        self, open_positions: list, category: str
    ) -> int:
        """Count positions in the same category opened within the last 48 hours."""
        if not category:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.category_window_hours)
        count = 0
        for pos in open_positions:
            if pos.get("category") == category:
                opened_at = pos.get("opened_at")
                if opened_at:
                    if isinstance(opened_at, str):
                        opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                    if opened_at >= cutoff:
                        count += 1
        return count

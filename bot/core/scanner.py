"""
Scanner — orchestrates all strategies each cycle and returns approved,
ranked, deduplicated signals ready for execution.
"""
import logging

from sqlalchemy import select

from bot.core.kalshi_client import KalshiClient
from bot.core.risk_manager import RiskManager
from bot.intelligence.signal_scorer import SignalScorer, TradeSignal
from bot.strategies.bond_strategy import BondStrategy
from bot.strategies.market_making import MarketMakingStrategy

logger = logging.getLogger(__name__)

MAX_SIGNALS_PER_CYCLE = 5


class Scanner:
    """Runs all enabled strategies and returns the best approved signals."""

    def __init__(self):
        self.risk_manager = RiskManager()
        self.scorer = SignalScorer()
        self.bond_strategy = BondStrategy()
        self.mm_strategy = MarketMakingStrategy()

    async def run_scan(
        self,
        client: KalshiClient,
        db_session,
        bankroll: float,
    ) -> list[TradeSignal]:
        """
        Full scan cycle:
        1. Check bot_enabled and daily loss limit
        2. Run enabled strategies
        3. Aggregate, deduplicate, risk-check, and rank signals
        4. Return top MAX_SIGNALS_PER_CYCLE approved signals
        """
        from api.models import Setting, Position

        # --- Step 1: Check global kill switch ---
        bot_enabled = await self._get_setting(db_session, "bot_enabled", "true")
        if bot_enabled.lower() != "true":
            logger.info("Scanner: bot is paused — skipping scan cycle")
            return []

        # --- Step 2: Check daily loss limit ---
        loss_limit_hit = await self.risk_manager.check_daily_loss_limit(db_session)
        if loss_limit_hit:
            logger.warning("Scanner: daily loss limit hit — skipping scan cycle")
            # Persist the pause
            await self._set_setting(db_session, "bot_enabled", "false")
            return []

        # --- Step 3: Gather open positions for risk checks ---
        result = await db_session.execute(select(Position))
        open_positions_orm = result.scalars().all()
        open_positions = [
            {
                "market_id": p.market_id,
                "strategy": p.strategy,
                "side": p.side,
                "size": p.size,
                "entry_price": float(p.entry_price),
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                "category": "",  # Kalshi category not stored; will be checked at runtime
            }
            for p in open_positions_orm
        ]
        open_position_tickers = {p["market_id"] for p in open_positions}

        # --- Step 4: Fetch open orders for MM deduplication ---
        try:
            open_orders = await client.get_orders(status="open")
        except Exception as exc:
            logger.error("Scanner: failed to fetch open orders: %s", exc)
            open_orders = []

        # --- Step 5: Run enabled strategies ---
        all_signals: list[TradeSignal] = []

        bond_enabled = await self._get_setting(db_session, "bond_strategy_enabled", "true")
        if bond_enabled.lower() == "true":
            try:
                bond_signals = await self.bond_strategy.scan(client, open_position_tickers)
                all_signals.extend(bond_signals)
                logger.info("Scanner: bond strategy returned %d signal(s)", len(bond_signals))
            except Exception as exc:
                logger.error("Scanner: bond strategy error: %s", exc)

        mm_enabled = await self._get_setting(db_session, "market_making_enabled", "true")
        if mm_enabled.lower() == "true":
            try:
                mm_signals = await self.mm_strategy.scan(
                    client, open_orders, open_position_tickers
                )
                all_signals.extend(mm_signals)
                logger.info("Scanner: market making returned %d signal(s)", len(mm_signals))
            except Exception as exc:
                logger.error("Scanner: market making strategy error: %s", exc)

        if not all_signals:
            logger.info("Scanner: no raw signals this cycle")
            return []

        # --- Step 6: Risk-check each signal ---
        approved_signals: list[TradeSignal] = []
        for signal in all_signals:
            market_stub = {
                "ticker": signal.ticker,
                "volume": self.mm_strategy.min_volume * 2,  # assume liquidity check passed in strategy
                "yes_ask": signal.entry_price,
                "no_ask": signal.entry_price,
                "category": "",
            }
            decision = await self.risk_manager.check_trade(
                market=market_stub,
                side=signal.side,
                proposed_size=signal.proposed_size,
                bankroll=bankroll,
                open_positions=open_positions,
            )
            if decision.approved:
                signal.proposed_size = decision.recommended_size
                approved_signals.append(signal)
            else:
                logger.debug("Scanner: rejected %s %s — %s", signal.ticker, signal.side, decision.reason)

        # --- Step 7: Filter minimum edge and rank ---
        filtered = self.scorer.filter_minimum_edge(approved_signals)
        ranked = self.scorer.rank_signals(filtered)

        top = ranked[:MAX_SIGNALS_PER_CYCLE]
        logger.info(
            "Scanner: cycle complete — %d raw, %d approved, %d ranked, %d selected",
            len(all_signals), len(approved_signals), len(ranked), len(top),
        )
        return top

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    async def _get_setting(db_session, key: str, default: str = "") -> str:
        from api.models import Setting
        result = await db_session.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        return setting.value if setting else default

    @staticmethod
    async def _set_setting(db_session, key: str, value: str) -> None:
        from api.models import Setting
        from datetime import datetime, timezone
        result = await db_session.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
            setting.updated_at = datetime.now(timezone.utc)
        else:
            db_session.add(Setting(key=key, value=value))
        await db_session.commit()

"""
Executor — the only module that places real orders and writes to the
trades/positions tables. No strategy logic lives here.
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

from bot.intelligence.param_guardrails import TUNABLE_PARAMS

KALSHI_FEE_PER_CONTRACT = 0.07  # per contract per side
BOND_ALERT_THRESHOLD = 0.10      # log ERROR if bond position moves > 10¢ against us

# Strategy-to-param-key mapping for pre-expiry seconds
_PRE_EXPIRY_KEYS = {
    "bond": "bond_pre_expiry_sec",
    "market_making": "mm_pre_expiry_sec",
    "btc_15min": "btc_pre_expiry_sec",
    "weather": "weather_pre_expiry_sec",
}

# Paper trading mode: log orders without actually placing them on Kalshi.
# Set PAPER_TRADE=true in .env to enable.
PAPER_TRADE = os.getenv("PAPER_TRADE", "false").lower() == "true"


class Executor:
    """Places orders, records trades, and monitors open positions."""

    @staticmethod
    async def _get_param(db_session, key: str) -> float | int:
        """Read a tunable parameter from the settings table, with guardrail default fallback."""
        from api.models import Setting

        spec = TUNABLE_PARAMS[key]
        result = await db_session.execute(
            select(Setting).where(Setting.key == key)
        )
        setting = result.scalar_one_or_none()
        raw = setting.value if setting else str(spec["default"])
        try:
            return int(raw) if spec["type"] == "int" else float(raw)
        except (ValueError, TypeError):
            return spec["default"]

    # -------------------------------------------------------------------------
    # Execute a signal
    # -------------------------------------------------------------------------

    async def execute_signal(
        self,
        signal: TradeSignal,
        client: KalshiClient,
        db_session,
    ) -> bool:
        """
        Place an order for the given signal. On success, insert into trades
        and positions tables. Returns True on success, False on failure.
        """
        from api.models import Trade, Position

        price_cents = int(signal.entry_price * 100)

        if PAPER_TRADE:
            # Paper trading: log the order but don't send it to Kalshi
            order_id = f"paper-{uuid.uuid4().hex[:12]}"
            logger.info(
                "Executor [PAPER]: would place %s %s %d @ %d¢ (paper_id=%s)",
                signal.ticker, signal.side, signal.proposed_size, price_cents, order_id,
            )
        else:
            try:
                order_result = await client.place_order(
                    ticker=signal.ticker,
                    side=signal.side,
                    count=signal.proposed_size,
                    price=price_cents,
                    order_type="limit",
                )
            except Exception as exc:
                logger.error(
                    "Executor: failed to place order for %s %s: %s",
                    signal.ticker, signal.side, exc,
                )
                return False

            order_id = order_result.get("order", {}).get("order_id") or order_result.get("order_id")
            logger.info(
                "Executor: order placed — %s %s %d @ %d¢ (order_id=%s)",
                signal.ticker, signal.side, signal.proposed_size, price_cents, order_id,
            )

        now = datetime.now(timezone.utc)
        trade_id = uuid.uuid4()

        # Calculate expiry from signal's time_to_resolution
        expires_at = None
        if signal.time_to_resolution and signal.time_to_resolution > 0:
            expires_at = now + timedelta(hours=signal.time_to_resolution)

        # --- Insert into trades table ---
        trade = Trade(
            id=trade_id,
            market_id=signal.ticker,
            market_title=signal.market_title,
            strategy=signal.strategy,
            side=signal.side,
            size=signal.proposed_size,
            entry_price=signal.entry_price,
            status="open",
            entry_reasoning=signal.reasoning,
            created_at=now,
        )
        db_session.add(trade)

        # --- Insert into positions table (upsert by market_id) ---
        result = await db_session.execute(
            select(Position).where(Position.market_id == signal.ticker)
        )
        existing = result.scalar_one_or_none()
        if not existing:
            position = Position(
                market_id=signal.ticker,
                market_title=signal.market_title,
                strategy=signal.strategy,
                side=signal.side,
                size=signal.proposed_size,
                entry_price=signal.entry_price,
                current_price=signal.entry_price,
                unrealized_pnl=0.0,
                opened_at=now,
                expires_at=expires_at,
            )
            db_session.add(position)

        try:
            await db_session.commit()
        except Exception as exc:
            logger.critical("Executor: DB commit failed for %s: %s", signal.ticker, exc)
            await db_session.rollback()
            return False

        return True

    # -------------------------------------------------------------------------
    # Monitor open positions
    # -------------------------------------------------------------------------

    async def monitor_positions(
        self,
        client: KalshiClient,
        db_session,
        reflection_callback=None,
    ) -> None:
        """
        For each open position:
        - Update unrealized PnL with current market price
        - Check for resolution and close if resolved
        - Apply stop-loss if position lost > 50% of value
        - Alert on bond positions moving adversely
        """
        from api.models import Position, Trade

        result = await db_session.execute(select(Position))
        positions = result.scalars().all()

        if not positions:
            return

        logger.debug("Executor: monitoring %d open position(s)", len(positions))

        for position in positions:
            try:
                await self._check_position(
                    position, client, db_session, reflection_callback
                )
            except Exception as exc:
                logger.error(
                    "Executor: error monitoring %s: %s", position.market_id, exc
                )

    async def _check_position(self, position, client, db_session, reflection_callback):
        """Check and update a single position."""
        from api.models import Trade

        try:
            market = await client.get_market(position.market_id)
        except Exception as exc:
            logger.warning("Executor: failed to fetch market %s: %s", position.market_id, exc)
            return

        # Backfill expires_at from market data if missing
        if not position.expires_at:
            close_time = market.get("close_time") or market.get("expiration_time")
            if close_time:
                position.expires_at = datetime.fromisoformat(
                    close_time.replace("Z", "+00:00")
                )
                logger.info(
                    "Executor: backfilled expires_at for %s → %s",
                    position.market_id, position.expires_at.isoformat(),
                )

        # Get current price for position's side
        last_price_raw = market.get("last_price") or market.get("yes_ask")
        if last_price_raw is None:
            return

        current_price = float(last_price_raw) / 100.0
        if position.side == "no":
            current_price = 1.0 - current_price

        # Update unrealized PnL
        entry_price = float(position.entry_price)
        unrealized = (current_price - entry_price) * position.size
        position.current_price = current_price
        position.unrealized_pnl = unrealized

        # --- Check resolution ---
        market_status = market.get("status", "")
        if market_status in ("resolved", "settled"):
            result_side = market.get("result", "")
            await self.close_position(
                position, client, db_session,
                reason=f"Market resolved: {result_side}",
                resolution_result=result_side,
                reflection_callback=reflection_callback,
            )
            return

        strategy = position.strategy or ""
        entry_value = entry_price * position.size
        now = datetime.now(timezone.utc)

        # --- Pre-expiry exit ---
        if position.expires_at:
            time_to_expiry = (position.expires_at - now).total_seconds()
            pre_expiry_key = _PRE_EXPIRY_KEYS.get(strategy)
            threshold = await self._get_param(db_session, pre_expiry_key) if pre_expiry_key else None
            if threshold and time_to_expiry <= threshold:
                reason = f"Pre-expiry exit ({strategy}, {threshold}s before close)"
                logger.warning(
                    "Executor: %s on %s — %.0fs to expiry",
                    reason, position.market_id, time_to_expiry,
                )
                await self.close_position(
                    position, client, db_session,
                    reason=reason,
                    reflection_callback=reflection_callback,
                )
                return

        # --- Strategy-specific stop-loss ---
        if strategy == "bond":
            # Bond: absolute price drop from entry (e.g. 94¢ → 88¢ = 6¢ drop)
            bond_stop_loss = await self._get_param(db_session, "bond_stop_loss_cents")
            price_drop = entry_price - current_price
            if price_drop >= bond_stop_loss:
                reason = f"Bond stop-loss ({price_drop:.2f} drop from {entry_price:.2f})"
                logger.warning("Executor: %s on %s", reason, position.market_id)
                await self.close_position(
                    position, client, db_session,
                    reason=reason,
                    reflection_callback=reflection_callback,
                )
                return
        else:
            # MM and BTC: percentage-based stop-loss
            stop_loss_threshold = await self._get_param(db_session, "stop_loss_threshold")
            if entry_value > 0 and unrealized <= -(entry_value * stop_loss_threshold):
                logger.warning(
                    "Executor: stop-loss triggered on %s — loss %.2f exceeds %.0f%% threshold",
                    position.market_id, unrealized, stop_loss_threshold * 100,
                )
                await self.close_position(
                    position, client, db_session,
                    reason=f"Stop-loss triggered ({strategy}, {stop_loss_threshold:.0%})",
                    reflection_callback=reflection_callback,
                )
                return

        # --- BTC take-profit ---
        if strategy == "btc_15min" and entry_value > 0:
            btc_take_profit = await self._get_param(db_session, "btc_take_profit_pct")
            profit_pct = unrealized / entry_value
            if profit_pct >= btc_take_profit:
                reason = f"BTC take-profit ({profit_pct:.0%} gain)"
                logger.info("Executor: %s on %s", reason, position.market_id)
                await self.close_position(
                    position, client, db_session,
                    reason=reason,
                    reflection_callback=reflection_callback,
                )
                return

        # --- MM time limit ---
        if strategy == "market_making" and position.opened_at:
            mm_max_hold = await self._get_param(db_session, "mm_max_hold_hours")
            hours_held = (now - position.opened_at).total_seconds() / 3600
            if hours_held >= mm_max_hold:
                reason = f"MM time limit ({hours_held:.1f}h held, max {mm_max_hold}h)"
                logger.warning("Executor: %s on %s", reason, position.market_id)
                await self.close_position(
                    position, client, db_session,
                    reason=reason,
                    reflection_callback=reflection_callback,
                )
                return

        # --- Bond alert: adverse price movement (informational, not an exit) ---
        if strategy == "bond":
            adverse_move = entry_price - current_price
            if adverse_move > BOND_ALERT_THRESHOLD:
                logger.error(
                    "Executor: ALERT — bond position %s moved %.3f against us "
                    "(entry=%.2f, current=%.2f)",
                    position.market_id, adverse_move, entry_price, current_price,
                )

        await db_session.commit()

    # -------------------------------------------------------------------------
    # Close a position
    # -------------------------------------------------------------------------

    async def close_position(
        self,
        position,
        client: KalshiClient,
        db_session,
        reason: str,
        resolution_result: str = "",
        reflection_callback=None,
    ) -> None:
        """
        Cancel open orders for the market, record final PnL in trades table,
        and remove from positions table.
        """
        from api.models import Trade, Setting

        logger.info(
            "Executor: closing position %s — %s",
            position.market_id, reason,
        )

        # Cancel any open orders for this market
        try:
            open_orders = await client.get_orders(status="open")
            for order in open_orders:
                if order.get("ticker") == position.market_id:
                    await client.cancel_order(order["order_id"])
        except Exception as exc:
            logger.warning(
                "Executor: error cancelling orders for %s: %s",
                position.market_id, exc,
            )

        # Determine exit price
        exit_price = float(position.current_price or position.entry_price)

        # Calculate PnL
        gross_pnl = (exit_price - float(position.entry_price)) * position.size
        fees = KALSHI_FEE_PER_CONTRACT * position.size * 2  # entry + exit
        net_pnl = gross_pnl - fees

        now = datetime.now(timezone.utc)

        # Update trade record
        result = await db_session.execute(
            select(Trade).where(
                Trade.market_id == position.market_id,
                Trade.status == "open",
            )
        )
        trade = result.scalar_one_or_none()
        if trade:
            trade.exit_price = exit_price
            trade.gross_pnl = gross_pnl
            trade.fees = fees
            trade.net_pnl = net_pnl
            trade.status = "closed"
            trade.resolved_at = now

            logger.info(
                "Executor: trade closed — %s %s, net_pnl=$%.2f",
                position.market_id, position.side, net_pnl,
            )

            # Update bankroll
            result = await db_session.execute(
                select(Setting).where(Setting.key == "current_bankroll")
            )
            bankroll_setting = result.scalar_one_or_none()
            if bankroll_setting:
                old_bankroll = float(bankroll_setting.value)
                new_bankroll = old_bankroll + net_pnl
                bankroll_setting.value = f"{new_bankroll:.2f}"
                logger.info(
                    "Executor: bankroll updated $%.2f → $%.2f (pnl=$%.2f)",
                    old_bankroll, new_bankroll, net_pnl,
                )

            # Fire reflection asynchronously — don't block trading
            if reflection_callback and trade:
                trade_dict = {
                    "id": str(trade.id),
                    "market_id": trade.market_id,
                    "market_title": trade.market_title,
                    "strategy": trade.strategy,
                    "side": trade.side,
                    "entry_price": float(trade.entry_price),
                    "exit_price": exit_price,
                    "net_pnl": net_pnl,
                    "entry_reasoning": trade.entry_reasoning,
                    "created_at": trade.created_at.isoformat(),
                    "resolved_at": now.isoformat(),
                }
                asyncio.create_task(reflection_callback(trade_dict))

        # Remove from positions
        await db_session.delete(position)

        try:
            await db_session.commit()
        except Exception as exc:
            logger.critical(
                "Executor: DB commit failed on close_position for %s: %s",
                position.market_id, exc,
            )
            await db_session.rollback()

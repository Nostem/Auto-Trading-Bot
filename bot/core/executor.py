"""
Executor — the only module that places real orders and writes to the
trades/positions tables. No strategy logic lives here.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from bot.core.kalshi_client import KalshiClient
from bot.intelligence.signal_scorer import TradeSignal

logger = logging.getLogger(__name__)

KALSHI_FEE_PER_CONTRACT = 0.07  # per contract per side
STOP_LOSS_THRESHOLD = 0.50       # exit if position loses > 50% of entry value
BOND_ALERT_THRESHOLD = 0.10      # log ERROR if bond position moves > 10¢ against us


class Executor:
    """Places orders, records trades, and monitors open positions."""

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

        # Get current price for position's side
        last_price_raw = market.get("last_price") or market.get("yes_ask")
        if last_price_raw is None:
            return

        current_price = float(last_price_raw) / 100.0
        if position.side == "no":
            current_price = 1.0 - current_price

        # Update unrealized PnL
        unrealized = (current_price - float(position.entry_price)) * position.size
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

        # --- Stop loss: position lost > 50% of entry value ---
        entry_value = float(position.entry_price) * position.size
        if entry_value > 0 and unrealized <= -(entry_value * STOP_LOSS_THRESHOLD):
            logger.warning(
                "Executor: stop-loss triggered on %s — loss %.2f exceeds %.0f%% threshold",
                position.market_id, unrealized, STOP_LOSS_THRESHOLD * 100,
            )
            await self.close_position(
                position, client, db_session,
                reason="Stop-loss triggered",
                reflection_callback=reflection_callback,
            )
            return

        # --- Bond alert: adverse price movement ---
        if position.strategy == "bond":
            adverse_move = float(position.entry_price) - current_price
            if adverse_move > BOND_ALERT_THRESHOLD:
                logger.error(
                    "Executor: ALERT — bond position %s moved %.3f against us "
                    "(entry=%.2f, current=%.2f)",
                    position.market_id, adverse_move,
                    float(position.entry_price), current_price,
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
        from api.models import Trade

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

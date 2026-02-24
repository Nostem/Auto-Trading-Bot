"""Dashboard stats endpoint."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import Position, Setting, Trade

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Current bankroll from settings
    result = await db.execute(select(Setting).where(Setting.key == "current_bankroll"))
    bankroll_setting = result.scalar_one_or_none()
    bankroll = float(bankroll_setting.value) if bankroll_setting else 0.0

    # Total PnL (all closed trades)
    result = await db.execute(
        select(func.sum(Trade.net_pnl)).where(Trade.status == "closed")
    )
    total_pnl = float(result.scalar() or 0)

    # Today's PnL
    result = await db.execute(
        select(func.sum(Trade.net_pnl)).where(
            Trade.status == "closed",
            Trade.resolved_at >= today_start,
        )
    )
    today_pnl = float(result.scalar() or 0)

    # Win rate
    result = await db.execute(
        select(func.count()).where(Trade.status == "closed")
    )
    total_trades = int(result.scalar() or 0)

    result = await db.execute(
        select(func.count()).where(Trade.status == "closed", Trade.net_pnl > 0)
    )
    wins = int(result.scalar() or 0)
    win_rate = (wins / total_trades * 100) if total_trades else 0.0

    # Open positions
    result = await db.execute(select(func.count(Position.id)))
    open_positions = int(result.scalar() or 0)

    result = await db.execute(select(func.sum(Position.unrealized_pnl)))
    unrealized_pnl = float(result.scalar() or 0)

    # Best strategy by net PnL
    result = await db.execute(
        select(Trade.strategy, func.sum(Trade.net_pnl).label("total"))
        .where(Trade.status == "closed")
        .group_by(Trade.strategy)
        .order_by(func.sum(Trade.net_pnl).desc())
        .limit(1)
    )
    row = result.first()
    best_strategy = row[0] if row else "none"

    # Win/loss streak (last N closed trades)
    result = await db.execute(
        select(Trade.net_pnl)
        .where(Trade.status == "closed")
        .order_by(Trade.resolved_at.desc())
        .limit(20)
    )
    recent_pnls = [float(r[0] or 0) for r in result.fetchall()]

    streak = 0
    if recent_pnls:
        direction = 1 if recent_pnls[0] > 0 else -1
        for pnl in recent_pnls:
            if (pnl > 0 and direction == 1) or (pnl <= 0 and direction == -1):
                streak += direction
            else:
                break

    return {
        "bankroll": bankroll,
        "total_pnl": total_pnl,
        "today_pnl": today_pnl,
        "win_rate": round(win_rate, 1),
        "total_trades": total_trades,
        "open_positions": open_positions,
        "unrealized_pnl": unrealized_pnl,
        "best_strategy": best_strategy,
        "streak": streak,
    }

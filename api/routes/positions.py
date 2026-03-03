"""Open positions endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import Position, Setting

router = APIRouter()


@router.get("/positions")
async def list_positions(
    run_id: str = Query("active"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Position)
    if run_id != "all":
        if run_id == "active":
            run_result = await db.execute(
                select(Setting).where(Setting.key == "active_run_id")
            )
            run_setting = run_result.scalar_one_or_none()
            run_id = run_setting.value if run_setting else "legacy"
        query = query.where(Position.run_id == run_id)

    result = await db.execute(query.order_by(Position.opened_at.desc()))
    positions = result.scalars().all()

    return [
        {
            "id": str(p.id),
            "market_id": p.market_id,
            "market_title": p.market_title,
            "strategy": p.strategy,
            "side": p.side,
            "size": p.size,
            "entry_price": float(p.entry_price),
            "current_price": float(p.current_price)
            if p.current_price is not None
            else None,
            "unrealized_pnl": float(p.unrealized_pnl)
            if p.unrealized_pnl is not None
            else None,
            "run_id": p.run_id,
            "strategy_version": p.strategy_version,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        }
        for p in positions
    ]

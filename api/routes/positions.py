"""Open positions endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import Position

router = APIRouter()


@router.get("/positions")
async def list_positions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Position).order_by(Position.opened_at.desc()))
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
            "current_price": float(p.current_price) if p.current_price is not None else None,
            "unrealized_pnl": float(p.unrealized_pnl) if p.unrealized_pnl is not None else None,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        }
        for p in positions
    ]

"""Trade history endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.database import get_db
from api.models import Reflection, Trade

router = APIRouter()


def _trade_to_dict(trade: Trade, reflection=None) -> dict:
    d = {
        "id": str(trade.id),
        "market_id": trade.market_id,
        "market_title": trade.market_title,
        "strategy": trade.strategy,
        "side": trade.side,
        "size": trade.size,
        "entry_price": float(trade.entry_price),
        "exit_price": float(trade.exit_price) if trade.exit_price is not None else None,
        "gross_pnl": float(trade.gross_pnl) if trade.gross_pnl is not None else None,
        "fees": float(trade.fees) if trade.fees is not None else None,
        "net_pnl": float(trade.net_pnl) if trade.net_pnl is not None else None,
        "status": trade.status,
        "entry_reasoning": trade.entry_reasoning,
        "created_at": trade.created_at.isoformat() if trade.created_at else None,
        "resolved_at": trade.resolved_at.isoformat() if trade.resolved_at else None,
    }
    if reflection:
        d["reflection"] = {
            "summary": reflection.summary,
            "what_worked": reflection.what_worked,
            "what_failed": reflection.what_failed,
            "confidence_score": reflection.confidence_score,
            "strategy_suggestion": reflection.strategy_suggestion,
        }
    return d


@router.get("/trades")
async def list_trades(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    strategy: str = Query("all"),
    status: str = Query("all"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade)

    if strategy != "all":
        query = query.where(Trade.strategy == strategy)
    if status != "all":
        query = query.where(Trade.status == status)

    query = query.order_by(Trade.created_at.desc())

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = int(total_result.scalar() or 0)

    # Paginate
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    trades = result.scalars().all()

    return {
        "trades": [_trade_to_dict(t) for t in trades],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total else 1,
    }


@router.get("/trades/{trade_id}")
async def get_trade(trade_id: str, db: AsyncSession = Depends(get_db)):
    try:
        tid = uuid.UUID(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trade ID")

    result = await db.execute(
        select(Trade).where(Trade.id == tid)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    # Fetch reflection
    ref_result = await db.execute(
        select(Reflection).where(Reflection.trade_id == tid).limit(1)
    )
    reflection = ref_result.scalar_one_or_none()

    return _trade_to_dict(trade, reflection)

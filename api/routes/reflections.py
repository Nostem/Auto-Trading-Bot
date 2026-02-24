"""AI reflection log endpoints."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import Reflection, WeeklyReflection

router = APIRouter()


@router.get("/reflections")
async def list_reflections(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    count_result = await db.execute(select(func.count(Reflection.id)))
    total = int(count_result.scalar() or 0)

    result = await db.execute(
        select(Reflection)
        .order_by(Reflection.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    reflections = result.scalars().all()

    return {
        "reflections": [
            {
                "id": str(r.id),
                "trade_id": str(r.trade_id) if r.trade_id else None,
                "summary": r.summary,
                "what_worked": r.what_worked,
                "what_failed": r.what_failed,
                "confidence_score": r.confidence_score,
                "strategy_suggestion": r.strategy_suggestion,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reflections
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total else 1,
    }


@router.get("/reflections/weekly")
async def list_weekly_reflections(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WeeklyReflection).order_by(WeeklyReflection.week_start.desc())
    )
    reports = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "week_start": r.week_start.isoformat() if r.week_start else None,
            "week_end": r.week_end.isoformat() if r.week_end else None,
            "total_trades": r.total_trades,
            "win_rate": float(r.win_rate) if r.win_rate is not None else None,
            "net_pnl": float(r.net_pnl) if r.net_pnl is not None else None,
            "top_strategy": r.top_strategy,
            "summary": r.summary,
            "key_learnings": r.key_learnings,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]

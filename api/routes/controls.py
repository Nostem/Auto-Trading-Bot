"""Bot control endpoints — pause/resume, settings management, and recommendations."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import Recommendation, Setting
from bot.intelligence.param_guardrails import validate_proposed_value

router = APIRouter()


class RiskSettingsUpdate(BaseModel):
    max_position_pct: float = Field(ge=0.05, le=0.25)
    daily_loss_limit_pct: float = Field(ge=0.01, le=0.25)
    sizing_mode: str | None = Field(default=None, pattern=r"^(fixed_dollar|percentage)$")
    fixed_trade_amount: float | None = Field(default=None, ge=1, le=100)


async def _upsert_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
    else:
        db.add(Setting(key=key, value=value, updated_at=datetime.now(timezone.utc)))
    await db.commit()


@router.post("/controls/pause")
async def pause_bot(db: AsyncSession = Depends(get_db)):
    await _upsert_setting(db, "bot_enabled", "false")
    return {"status": "paused", "bot_enabled": False}


@router.post("/controls/resume")
async def resume_bot(db: AsyncSession = Depends(get_db)):
    await _upsert_setting(db, "bot_enabled", "true")
    return {"status": "active", "bot_enabled": True}


class StrategyToggle(BaseModel):
    key: str = Field(pattern=r"^(bond_strategy_enabled|market_making_enabled|btc_strategy_enabled|weather_strategy_enabled)$")
    enabled: bool


@router.post("/controls/strategy")
async def toggle_strategy(
    body: StrategyToggle,
    db: AsyncSession = Depends(get_db),
):
    await _upsert_setting(db, body.key, "true" if body.enabled else "false")
    return {"status": "updated", "key": body.key, "enabled": body.enabled}


@router.post("/controls/settings")
async def update_settings(
    body: RiskSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _upsert_setting(db, "max_position_pct", str(body.max_position_pct))
    await _upsert_setting(db, "daily_loss_limit_pct", str(body.daily_loss_limit_pct))
    if body.sizing_mode is not None:
        await _upsert_setting(db, "sizing_mode", body.sizing_mode)
    if body.fixed_trade_amount is not None:
        await _upsert_setting(db, "fixed_trade_amount", str(body.fixed_trade_amount))
    return {"status": "updated", **body.model_dump(exclude_none=True)}


@router.get("/controls/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting))
    settings = result.scalars().all()
    return {s.key: s.value for s in settings}


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class DenyRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


@router.get("/controls/recommendations")
async def list_recommendations(
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
):
    query = select(Recommendation).order_by(Recommendation.created_at.desc())
    if status != "all":
        query = query.where(Recommendation.status == status)
    result = await db.execute(query)
    recs = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "setting_key": r.setting_key,
            "current_value": r.current_value,
            "proposed_value": r.proposed_value,
            "reasoning": r.reasoning,
            "trigger": r.trigger,
            "status": r.status,
            "denial_reason": r.denial_reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recs
    ]


@router.post("/controls/recommendations/{rec_id}/approve")
async def approve_recommendation(
    rec_id: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        rec_uuid = uuid.UUID(rec_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid recommendation ID")

    result = await db.execute(
        select(Recommendation).where(Recommendation.id == rec_uuid)
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status != "pending":
        raise HTTPException(status_code=400, detail=f"Recommendation is already {rec.status}")

    # Validate against guardrails
    valid, err = validate_proposed_value(rec.setting_key, rec.proposed_value)
    if not valid:
        raise HTTPException(status_code=400, detail=err)

    # Apply the setting change
    await _upsert_setting(db, rec.setting_key, rec.proposed_value)

    # Mark recommendation as approved
    rec.status = "approved"
    rec.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status": "approved",
        "setting_key": rec.setting_key,
        "new_value": rec.proposed_value,
    }


@router.post("/controls/recommendations/{rec_id}/deny")
async def deny_recommendation(
    rec_id: str,
    body: DenyRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        rec_uuid = uuid.UUID(rec_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid recommendation ID")

    result = await db.execute(
        select(Recommendation).where(Recommendation.id == rec_uuid)
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status != "pending":
        raise HTTPException(status_code=400, detail=f"Recommendation is already {rec.status}")

    rec.status = "denied"
    rec.denial_reason = body.reason
    rec.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "denied", "setting_key": rec.setting_key}

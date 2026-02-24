"""Bot control endpoints — pause/resume and settings management."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import Setting

router = APIRouter()


class RiskSettingsUpdate(BaseModel):
    max_position_pct: float = Field(ge=0.05, le=0.25)
    daily_loss_limit_pct: float = Field(ge=0.01, le=0.10)


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


@router.post("/controls/settings")
async def update_settings(
    body: RiskSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _upsert_setting(db, "max_position_pct", str(body.max_position_pct))
    await _upsert_setting(db, "daily_loss_limit_pct", str(body.daily_loss_limit_pct))
    return {"status": "updated", **body.model_dump()}


@router.get("/controls/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting))
    settings = result.scalars().all()
    return {s.key: s.value for s in settings}

"""Shared bot state transition helpers.

State model:
- desired_state: operator intent
- effective_state: runtime-enforced state after safety checks
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import BotState, BotStateEvent, Setting

STATE_RUNNING = "RUNNING"
STATE_PAUSED_MANUAL = "PAUSED_MANUAL"
STATE_PAUSED_RISK = "PAUSED_RISK"
STATE_PAUSED_SYSTEM = "PAUSED_SYSTEM"


def is_truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def make_session_id(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return f"sess-{current.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


async def _get_setting(session: AsyncSession, key: str, default: str) -> str:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def _upsert_setting(session: AsyncSession, key: str, value: str) -> None:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
    else:
        session.add(
            Setting(key=key, value=value, updated_at=datetime.now(timezone.utc))
        )


async def get_or_create_bot_state(session: AsyncSession) -> BotState:
    result = await session.execute(select(BotState).where(BotState.id == 1))
    state = result.scalar_one_or_none()
    if state:
        return state

    active_run_id = await _get_setting(session, "active_run_id", "legacy")
    state = BotState(
        id=1,
        desired_state=STATE_RUNNING,
        effective_state=STATE_RUNNING,
        active_run_id=active_run_id,
        session_id=make_session_id(),
        updated_by="bootstrap",
        version=1,
    )
    session.add(state)
    await _upsert_setting(session, "bot_enabled", "true")
    return state


async def transition_bot_state(
    session: AsyncSession,
    *,
    desired_state: str | None = None,
    effective_state: str | None = None,
    reason: str | None = None,
    detail: str | None = None,
    source: str,
    actor_type: str,
    actor_id: str | None = None,
    run_id: str | None = None,
    new_session: bool = False,
) -> BotState:
    now = datetime.now(timezone.utc)
    state = await get_or_create_bot_state(session)
    prev_effective = state.effective_state

    if desired_state is not None:
        state.desired_state = desired_state
    if effective_state is not None:
        state.effective_state = effective_state
    if run_id is not None:
        state.active_run_id = run_id
    if new_session:
        state.session_id = make_session_id(now)

    state.pause_reason = reason
    state.pause_detail = detail
    state.last_transition_at = now
    state.updated_by = actor_type
    state.version = int(state.version or 0) + 1

    session.add(
        BotStateEvent(
            id=uuid.uuid4(),
            actor_type=actor_type,
            actor_id=actor_id,
            source=source,
            from_state=prev_effective,
            to_state=state.effective_state,
            reason=reason,
            detail={"message": detail} if detail else None,
            run_id=state.active_run_id,
            session_id=state.session_id,
            created_at=now,
        )
    )

    # Temporary compatibility mirror for existing controls/UI.
    legacy_enabled = "true" if state.effective_state == STATE_RUNNING else "false"
    await _upsert_setting(session, "bot_enabled", legacy_enabled)
    if new_session:
        await _upsert_setting(session, "bot_resumed_at", now.isoformat())
    await _upsert_setting(session, "active_run_id", state.active_run_id)

    return state

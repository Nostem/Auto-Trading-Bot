"""Tests for the recommendations API endpoints (controls.py)."""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database import Base, get_db
from api.models import Recommendation, Setting

# ---------------------------------------------------------------------------
# In-memory SQLite async engine for tests
# ---------------------------------------------------------------------------

TEST_ENGINE = create_async_engine("sqlite+aiosqlite://", echo=False)
TestSession = async_sessionmaker(TEST_ENGINE, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create all tables before each test and drop after."""
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    """FastAPI test client with DB override and auth disabled."""
    import os
    os.environ["API_BEARER_TOKEN"] = ""  # disable auth for tests

    # Re-import to pick up env change
    from api.main import app
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def sample_recommendation():
    """Insert a pending recommendation and return its ID."""
    rec_id = uuid.uuid4()
    async with TestSession() as session:
        # Seed a current setting value
        session.add(Setting(key="bond_stop_loss_cents", value="0.06"))
        session.add(Recommendation(
            id=rec_id,
            setting_key="bond_stop_loss_cents",
            current_value="0.06",
            proposed_value="0.04",
            reasoning="Recent bond losses suggest tighter stop-loss",
            trigger="weekly_report",
            status="pending",
        ))
        await session.commit()
    return str(rec_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recommendations_empty(client):
    resp = await client.get("/controls/recommendations")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_recommendations_with_data(client, sample_recommendation):
    resp = await client.get("/controls/recommendations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == sample_recommendation
    assert data[0]["status"] == "pending"
    assert data[0]["setting_key"] == "bond_stop_loss_cents"


@pytest.mark.asyncio
async def test_list_recommendations_filter_status(client, sample_recommendation):
    resp = await client.get("/controls/recommendations?status=approved")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = await client.get("/controls/recommendations?status=all")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_approve_recommendation(client, sample_recommendation):
    resp = await client.post(f"/controls/recommendations/{sample_recommendation}/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["new_value"] == "0.04"

    # Verify setting was updated
    async with TestSession() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "bond_stop_loss_cents")
        )
        setting = result.scalar_one()
        assert setting.value == "0.04"

    # Verify recommendation is marked approved
    async with TestSession() as session:
        result = await session.execute(
            select(Recommendation).where(Recommendation.id == uuid.UUID(sample_recommendation))
        )
        rec = result.scalar_one()
        assert rec.status == "approved"
        assert rec.resolved_at is not None


@pytest.mark.asyncio
async def test_approve_already_approved(client, sample_recommendation):
    await client.post(f"/controls/recommendations/{sample_recommendation}/approve")
    resp = await client.post(f"/controls/recommendations/{sample_recommendation}/approve")
    assert resp.status_code == 400
    assert "already approved" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deny_recommendation(client, sample_recommendation):
    resp = await client.post(
        f"/controls/recommendations/{sample_recommendation}/deny",
        json={"reason": "Too aggressive for current market"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"

    # Verify denial reason saved
    async with TestSession() as session:
        result = await session.execute(
            select(Recommendation).where(Recommendation.id == uuid.UUID(sample_recommendation))
        )
        rec = result.scalar_one()
        assert rec.status == "denied"
        assert rec.denial_reason == "Too aggressive for current market"
        assert rec.resolved_at is not None


@pytest.mark.asyncio
async def test_deny_requires_reason(client, sample_recommendation):
    resp = await client.post(
        f"/controls/recommendations/{sample_recommendation}/deny",
        json={"reason": ""},
    )
    assert resp.status_code == 422  # validation error


@pytest.mark.asyncio
async def test_approve_nonexistent(client):
    fake_id = str(uuid.uuid4())
    resp = await client.post(f"/controls/recommendations/{fake_id}/approve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_invalid_id(client):
    resp = await client.post("/controls/recommendations/not-a-uuid/approve")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_approve_invalid_guardrail(client):
    """Recommendation with out-of-bounds value should be rejected."""
    rec_id = uuid.uuid4()
    async with TestSession() as session:
        session.add(Recommendation(
            id=rec_id,
            setting_key="bond_stop_loss_cents",
            current_value="0.06",
            proposed_value="0.50",  # way above max of 0.10
            reasoning="test",
            trigger="weekly_report",
            status="pending",
        ))
        await session.commit()

    resp = await client.post(f"/controls/recommendations/{rec_id}/approve")
    assert resp.status_code == 400
    assert "above maximum" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_strategy_toggle(client):
    """Test the new strategy toggle endpoint."""
    # Seed the setting
    async with TestSession() as session:
        session.add(Setting(key="bond_strategy_enabled", value="true"))
        await session.commit()

    resp = await client.post(
        "/controls/strategy",
        json={"key": "bond_strategy_enabled", "enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # Verify DB
    async with TestSession() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "bond_strategy_enabled")
        )
        assert result.scalar_one().value == "false"


@pytest.mark.asyncio
async def test_strategy_toggle_invalid_key(client):
    resp = await client.post(
        "/controls/strategy",
        json={"key": "invalid_key", "enabled": True},
    )
    assert resp.status_code == 422

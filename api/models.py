"""
SQLAlchemy ORM models matching the db/schema.sql tables.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    market_title: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    fees: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    entry_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    reflections: Mapped[list["Reflection"]] = relationship(
        "Reflection", back_populates="trade", lazy="select"
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    market_title: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    current_price: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Reflection(Base):
    __tablename__ = "reflections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trades.id"), nullable=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    what_worked: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    trade: Mapped["Trade | None"] = relationship(
        "Trade", back_populates="reflections"
    )


class WeeklyReflection(Base):
    __tablename__ = "weekly_reflections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_start: Mapped[datetime] = mapped_column(Date, nullable=False)
    week_end: Mapped[datetime] = mapped_column(Date, nullable=False)
    total_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    top_strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_learnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    setting_key: Mapped[str] = mapped_column(String(100), nullable=False)
    current_value: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_value: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    denial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

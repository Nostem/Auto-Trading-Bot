"""
Reflection Engine — uses Claude to generate trade post-mortems and weekly
summary reports. Runs non-blocking; never delays trade execution.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import anthropic
from sqlalchemy import select, func
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_REFLECT_SYSTEM = (
    "You are a trading journal AI for a prediction market bot. "
    "Analyze trades honestly and provide actionable insights. Return JSON only."
)

_REFLECT_USER_TEMPLATE = """Analyze this completed trade:
Market: {market_title}
Strategy: {strategy}
Side: {side}
Entry Price: {entry_price:.2f}
Exit Price: {exit_price:.2f}
Net PnL: ${net_pnl:.2f}
Result: {result}
Original Reasoning: {entry_reasoning}
Time Held: {hours:.1f} hours

Return JSON: {{"summary": "2 sentence summary", "what_worked": "what went right or null", "what_failed": "what went wrong or null", "confidence_score": 1-10, "strategy_suggestion": "one actionable improvement for next time"}}"""

_WEEKLY_SYSTEM = (
    "You are a trading performance analyst for a prediction market bot. "
    "Provide honest, data-driven weekly summaries. Return JSON only."
)


class ReflectionEngine:
    """Generates AI-powered trade post-mortems and weekly performance reports."""

    def __init__(self):
        self._claude = anthropic.AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "")
        )

    # -------------------------------------------------------------------------
    # Single trade reflection
    # -------------------------------------------------------------------------

    async def reflect_on_trade(self, trade: dict, db_session) -> None:
        """
        Generate a Claude reflection for a resolved trade and write it
        to the reflections table. Non-blocking — errors are logged only.
        """
        from api.models import Reflection

        trade_id = trade.get("id")
        market_title = trade.get("market_title", "Unknown")

        logger.info("ReflectionEngine: generating reflection for trade %s", trade_id)

        try:
            hours_held = self._hours_between(
                trade.get("created_at"), trade.get("resolved_at")
            )
            net_pnl = float(trade.get("net_pnl", 0))
            entry_price = float(trade.get("entry_price", 0))
            exit_price = float(trade.get("exit_price", entry_price))

            prompt = _REFLECT_USER_TEMPLATE.format(
                market_title=market_title,
                strategy=trade.get("strategy", "unknown"),
                side=trade.get("side", "unknown"),
                entry_price=entry_price,
                exit_price=exit_price,
                net_pnl=net_pnl,
                result="WIN" if net_pnl > 0 else "LOSS",
                entry_reasoning=trade.get("entry_reasoning", "No reasoning provided"),
                hours=hours_held,
            )

            response = await self._claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=500,
                system=_REFLECT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            data = self._parse_json(raw)

        except Exception as exc:
            logger.error(
                "ReflectionEngine: Claude API error for trade %s: %s", trade_id, exc
            )
            # Write a fallback reflection so we always have a record
            data = {
                "summary": f"Trade {'won' if float(trade.get('net_pnl', 0)) > 0 else 'lost'} ${abs(float(trade.get('net_pnl', 0))):.2f}.",
                "what_worked": None,
                "what_failed": "Reflection generation failed.",
                "confidence_score": 5,
                "strategy_suggestion": "Review trade manually.",
            }

        reflection = Reflection(
            trade_id=uuid.UUID(trade_id) if trade_id else None,
            summary=data.get("summary", ""),
            what_worked=data.get("what_worked"),
            what_failed=data.get("what_failed"),
            confidence_score=int(data.get("confidence_score", 5)),
            strategy_suggestion=data.get("strategy_suggestion"),
        )
        db_session.add(reflection)

        try:
            await db_session.commit()
            logger.info("ReflectionEngine: reflection saved for trade %s", trade_id)
        except Exception as exc:
            logger.error(
                "ReflectionEngine: DB error saving reflection for %s: %s", trade_id, exc
            )
            await db_session.rollback()

    # -------------------------------------------------------------------------
    # Weekly report
    # -------------------------------------------------------------------------

    async def generate_weekly_report(self, db_session) -> None:
        """
        Generate a weekly performance summary (run every Monday).
        Fetches last 7 days of trades + reflections, calls Claude, saves report.
        """
        from api.models import Trade, Reflection, WeeklyReflection

        now = datetime.now(timezone.utc)
        week_end = now.date()
        week_start = week_end - timedelta(days=7)

        week_start_dt = datetime(
            week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc
        )

        # Fetch week's closed trades
        result = await db_session.execute(
            select(Trade).where(
                Trade.resolved_at >= week_start_dt,
                Trade.status == "closed",
            )
        )
        trades = result.scalars().all()

        if not trades:
            logger.info("ReflectionEngine: no trades this week — skipping weekly report")
            return

        total_trades = len(trades)
        wins = sum(1 for t in trades if (t.net_pnl or 0) > 0)
        win_rate = (wins / total_trades * 100) if total_trades else 0.0
        net_pnl = sum(float(t.net_pnl or 0) for t in trades)

        # Best strategy by net PnL
        strategy_pnl: dict[str, float] = {}
        for t in trades:
            strategy_pnl[t.strategy] = strategy_pnl.get(t.strategy, 0.0) + float(t.net_pnl or 0)
        top_strategy = max(strategy_pnl, key=strategy_pnl.get) if strategy_pnl else "none"

        # Fetch recent reflections for context
        learnings = await self.get_recent_learnings(db_session, limit=10)

        weekly_prompt = f"""Weekly trading performance summary:
Period: {week_start} to {week_end}
Total trades: {total_trades}
Win rate: {win_rate:.1f}%
Net PnL: ${net_pnl:.2f}
Best strategy: {top_strategy}
Strategy breakdown: {strategy_pnl}

Recent trade reflections:
{learnings}

Return JSON: {{"summary": "3-4 sentence overview", "key_learnings": "2-3 bullet points of actionable insights"}}"""

        try:
            response = await self._claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=600,
                system=_WEEKLY_SYSTEM,
                messages=[{"role": "user", "content": weekly_prompt}],
            )
            raw = response.content[0].text.strip()
            data = self._parse_json(raw)
        except Exception as exc:
            logger.error("ReflectionEngine: weekly report Claude error: %s", exc)
            data = {
                "summary": f"Week of {week_start}: {total_trades} trades, {win_rate:.1f}% win rate, ${net_pnl:.2f} net PnL.",
                "key_learnings": "Manual review recommended.",
            }

        report = WeeklyReflection(
            week_start=week_start,
            week_end=week_end,
            total_trades=total_trades,
            win_rate=win_rate,
            net_pnl=net_pnl,
            top_strategy=top_strategy,
            summary=data.get("summary", ""),
            key_learnings=data.get("key_learnings", ""),
        )
        db_session.add(report)

        try:
            await db_session.commit()
            logger.info(
                "ReflectionEngine: weekly report saved for %s – %s", week_start, week_end
            )
        except Exception as exc:
            logger.error("ReflectionEngine: DB error saving weekly report: %s", exc)
            await db_session.rollback()

    # -------------------------------------------------------------------------
    # Recent learnings for strategy context injection
    # -------------------------------------------------------------------------

    async def get_recent_learnings(self, db_session, limit: int = 5) -> str:
        """Return the N most recent reflection summaries as a formatted string."""
        from api.models import Reflection

        result = await db_session.execute(
            select(Reflection)
            .order_by(Reflection.created_at.desc())
            .limit(limit)
        )
        reflections = result.scalars().all()

        if not reflections:
            return "No reflections yet."

        lines = []
        for r in reflections:
            lines.append(f"- [{r.created_at.date()}] {r.summary}")
            if r.strategy_suggestion:
                lines.append(f"  Suggestion: {r.strategy_suggestion}")
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Parse JSON from Claude response, stripping markdown fences if present."""
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            logger.warning("ReflectionEngine: JSON parse error: %s — raw: %s", exc, raw[:200])
            return {}

    @staticmethod
    def _hours_between(start_iso: str | None, end_iso: str | None) -> float:
        """Calculate hours between two ISO timestamps."""
        if not start_iso or not end_iso:
            return 0.0
        try:
            def parse(s):
                s = s.replace("Z", "+00:00")
                return datetime.fromisoformat(s)
            return (parse(end_iso) - parse(start_iso)).total_seconds() / 3600.0
        except Exception:
            return 0.0

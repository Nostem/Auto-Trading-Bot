"""
Reflection Engine — uses an LLM (Claude or MiniMax Coding Plan) to generate
trade post-mortems and weekly summary reports. Runs non-blocking; never delays
trade execution.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import anthropic
from sqlalchemy import select, and_
from dotenv import load_dotenv

from bot.intelligence.param_guardrails import TUNABLE_PARAMS, validate_proposed_value

load_dotenv()
logger = logging.getLogger(__name__)

# MiniMax Coding Plan is Anthropic API–compatible; use base_url + model when key is set
MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"
MINIMAX_MODEL = "minimax-m2.5-highspeed"
ANTHROPIC_MODEL = "claude-opus-4-6"

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

_RECOMMEND_SYSTEM = (
    "You are a parameter tuning advisor for a prediction market trading bot. "
    "Based on recent trade performance and current parameter values, suggest "
    "specific parameter changes that could improve results. Be conservative — "
    "only recommend changes with clear evidence. Return JSON only."
)


def _llm_client():
    """Return AsyncAnthropic client for MiniMax (Coding Plan) or Anthropic."""
    minimax_key = os.getenv("MINIMAX_CODING_PLAN_API_KEY", "").strip()
    if minimax_key:
        # MiniMax docs: use ANTHROPIC_BASE_URL + ANTHROPIC_API_KEY; SDK sends X-Api-Key
        return anthropic.AsyncAnthropic(
            api_key=minimax_key,
            base_url=MINIMAX_BASE_URL,
        ), MINIMAX_MODEL
    return (
        anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "")),
        ANTHROPIC_MODEL,
    )


def _first_text_from_content(content) -> str:
    """Get first text block from LLM response (MiniMax may return thinking + text)."""
    for block in content:
        if getattr(block, "type", None) == "text" and hasattr(block, "text"):
            return block.text
        if hasattr(block, "text"):
            return block.text
    return ""


class ReflectionEngine:
    """Generates AI-powered trade post-mortems and weekly performance reports."""

    def __init__(self):
        self._client, self._model = _llm_client()

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

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=500,
                system=_REFLECT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = _first_text_from_content(response.content).strip()
            data = self._parse_json(raw)

        except Exception as exc:
            logger.error(
                "ReflectionEngine: LLM API error for trade %s: %s", trade_id, exc
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
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=600,
                system=_WEEKLY_SYSTEM,
                messages=[{"role": "user", "content": weekly_prompt}],
            )
            raw = _first_text_from_content(response.content).strip()
            data = self._parse_json(raw)
        except Exception as exc:
            logger.error("ReflectionEngine: weekly report LLM error: %s", exc)
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
            return

        # Generate parameter recommendations after weekly report
        try:
            await self.generate_recommendations(db_session, trigger="weekly_report")
        except Exception as exc:
            logger.error("ReflectionEngine: post-weekly recommendations error: %s", exc)

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
    # Parameter recommendations
    # -------------------------------------------------------------------------

    async def generate_recommendations(self, db_session, trigger: str) -> None:
        """
        Ask the LLM to propose 0-3 parameter changes based on recent performance.
        Validates against guardrails, skips duplicates, writes to recommendations table.
        """
        from api.models import Trade, Reflection, Recommendation, Setting

        logger.info("ReflectionEngine: generating recommendations (trigger=%s)", trigger)

        try:
            # Gather last 20 closed trades
            result = await db_session.execute(
                select(Trade)
                .where(Trade.status == "closed")
                .order_by(Trade.resolved_at.desc())
                .limit(20)
            )
            recent_trades = result.scalars().all()

            if not recent_trades:
                logger.info("ReflectionEngine: no closed trades — skipping recommendations")
                return

            trades_summary = []
            for t in recent_trades:
                trades_summary.append({
                    "strategy": t.strategy,
                    "side": t.side,
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price) if t.exit_price else None,
                    "net_pnl": float(t.net_pnl) if t.net_pnl else 0,
                    "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                })

            # Recent reflections
            learnings = await self.get_recent_learnings(db_session, limit=10)

            # Current param values from DB
            current_params = {}
            for key in TUNABLE_PARAMS:
                result = await db_session.execute(
                    select(Setting).where(Setting.key == key)
                )
                setting = result.scalar_one_or_none()
                current_params[key] = setting.value if setting else str(TUNABLE_PARAMS[key]["default"])

            # Last 5 denied recommendations with user's reasoning
            result = await db_session.execute(
                select(Recommendation)
                .where(Recommendation.status == "denied")
                .order_by(Recommendation.resolved_at.desc())
                .limit(5)
            )
            denied = result.scalars().all()
            denied_context = ""
            if denied:
                denied_lines = []
                for d in denied:
                    denied_lines.append(
                        f"- {d.setting_key}: proposed {d.proposed_value} (denied: {d.denial_reason or 'no reason given'})"
                    )
                denied_context = (
                    "\n\nPreviously denied recommendations (respect the user's reasoning):\n"
                    + "\n".join(denied_lines)
                )

            # Build param descriptions for the LLM
            param_info = []
            for key, spec in TUNABLE_PARAMS.items():
                param_info.append(
                    f"- {key}: {spec['description']} "
                    f"(current={current_params[key]}, min={spec['min']}, max={spec['max']})"
                )

            prompt = f"""Recent closed trades (newest first):
{json.dumps(trades_summary, indent=2)}

Recent reflections:
{learnings}

Tunable parameters:
{chr(10).join(param_info)}
{denied_context}

Trigger: {trigger}

Based on the evidence above, propose 0-3 specific parameter changes.
Only propose changes with clear supporting evidence from the trade data.
Do NOT propose changes the user has recently denied for similar reasons.

Return JSON array: [{{"setting_key": "param_name", "proposed_value": "new_value", "reasoning": "why this change, citing specific trade data"}}]
Return an empty array [] if no changes are warranted."""

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=800,
                system=_RECOMMEND_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = _first_text_from_content(response.content).strip()
            recommendations = self._parse_json_array(raw)

        except Exception as exc:
            logger.error("ReflectionEngine: recommendation generation error: %s", exc)
            return

        # Validate and write each recommendation
        written = 0
        for rec in recommendations:
            key = rec.get("setting_key", "")
            proposed = rec.get("proposed_value", "")
            reasoning = rec.get("reasoning", "")

            if not key or not proposed or not reasoning:
                continue

            # Validate against guardrails
            valid, err = validate_proposed_value(key, proposed)
            if not valid:
                logger.info("ReflectionEngine: skipping invalid recommendation %s=%s: %s", key, proposed, err)
                continue

            # Skip no-ops (proposed == current)
            current = current_params.get(key, "")
            if str(proposed).strip() == str(current).strip():
                continue

            # Skip if pending recommendation for same key already exists
            result = await db_session.execute(
                select(Recommendation).where(
                    and_(
                        Recommendation.setting_key == key,
                        Recommendation.status == "pending",
                    )
                )
            )
            if result.scalar_one_or_none():
                logger.info("ReflectionEngine: skipping %s — pending recommendation already exists", key)
                continue

            recommendation = Recommendation(
                setting_key=key,
                current_value=current,
                proposed_value=str(proposed),
                reasoning=reasoning,
                trigger=trigger,
            )
            db_session.add(recommendation)
            written += 1

        if written:
            try:
                await db_session.commit()
                logger.info("ReflectionEngine: wrote %d recommendation(s) (trigger=%s)", written, trigger)
            except Exception as exc:
                logger.error("ReflectionEngine: DB error saving recommendations: %s", exc)
                await db_session.rollback()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _parse_json_array(raw: str) -> list[dict]:
        """Parse a JSON array from LLM response, stripping markdown fences."""
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            result = json.loads(raw.strip())
            return result if isinstance(result, list) else []
        except json.JSONDecodeError as exc:
            logger.warning("ReflectionEngine: JSON array parse error: %s — raw: %s", exc, raw[:200])
            return []

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

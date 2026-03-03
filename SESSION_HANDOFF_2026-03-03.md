# Session Handoff (2026-03-03)

## Deployment status
- Backend API/bot is live on Railway.
- Frontend is live on Vercel at `https://auto-trading-bot.vercel.app`.
- Vercel basic auth middleware is enabled (`ui/middleware.ts`).

## Key fixes completed
- Railway DB driver normalization + asyncpg enforcement.
- Added runtime migration locking to avoid deadlocks:
  - commit `781e7b2` (`api/database.py` advisory lock)
- Added run-scoped strategy testing framework + fee-aware gating:
  - commit `21c80ef`

## New run framework implemented
- Added `run_id` and `strategy_version` to:
  - `trades`, `positions`, `reflections`, `recommendations`, `weekly_reflections`
- Added runtime migrations and defaults in settings:
  - `active_run_id`
  - `active_strategy_version`
  - `min_expected_edge_buffer`
- API endpoints default to active run (`run_id=active`) for:
  - dashboard, trades, positions, reflections, recommendations

## Reset + rollover script
- New script: `scripts/start_new_run.py`
- It archives previous open state and starts a clean run without deleting history.

## Current active test run
- `run_id=paper-v2-20260303-070247`
- `strategy_version=v2`
- rollover result:
  - `archived_open_trades=52`
  - `deleted_positions=32`
  - `archived_pending_recommendations=5`
  - `current_bankroll=1000.00`
  - `daily_loss_limit_pct=0.2000`
  - `min_expected_edge_buffer=0.0100`
- Bot is now running (user resumed after rollover).

## Files changed in major strategy/reset update
- `api/database.py`
- `api/main.py`
- `api/models.py`
- `api/routes/controls.py`
- `api/routes/dashboard.py`
- `api/routes/positions.py`
- `api/routes/reflections.py`
- `api/routes/trades.py`
- `bot/core/executor.py`
- `bot/core/risk_manager.py`
- `bot/core/scanner.py`
- `bot/intelligence/reflection_engine.py`
- `bot/main.py`
- `db/schema.sql`
- `scripts/start_new_run.py`

## Tomorrow morning recommended checks
1. Verify `/dashboard` shows active `run_id` and fresh metrics.
2. Review overnight closed trades in active run only.
3. Review reflections/recommendations quality (active run only).
4. Decide whether to tighten/loosen `min_expected_edge_buffer` and strategy toggles.

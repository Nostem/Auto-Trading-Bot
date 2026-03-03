# Bot Rebuild Plan (State-Driven, Two-Service)

## Goals

- Eliminate ambiguous pause/resume behavior.
- Isolate API and worker lifecycle so one process cannot kill the other.
- Make state transitions auditable and deterministic.
- Keep trading path independent from LLM/reflection path.

---

## 1) Runtime Topology

### Railway services

1. `api-service`
   - Runs `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
   - No APScheduler, no scan loop, no Kalshi order flow.

2. `bot-worker`
   - Runs `python -m bot.main`
   - No HTTP server required.

### Why

- Removes `entrypoint.sh` dual-process coupling (`wait -n` failure mode).
- Independent deploy/restart/health behavior.

---

## 2) Data Model (Source of Truth)

### New table: `bot_state`

One row per environment (keyed by `id = 1`).

```sql
CREATE TABLE IF NOT EXISTS bot_state (
  id SMALLINT PRIMARY KEY CHECK (id = 1),
  desired_state VARCHAR(32) NOT NULL,
  effective_state VARCHAR(32) NOT NULL,
  pause_reason VARCHAR(64),
  pause_detail TEXT,
  active_run_id VARCHAR(64) NOT NULL,
  session_id VARCHAR(64),
  last_transition_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by VARCHAR(64) NOT NULL DEFAULT 'system',
  version BIGINT NOT NULL DEFAULT 1
);

INSERT INTO bot_state (
  id, desired_state, effective_state, pause_reason, pause_detail,
  active_run_id, session_id, last_transition_at, updated_by, version
)
VALUES (1, 'RUNNING', 'RUNNING', NULL, NULL, 'legacy', NULL, NOW(), 'migration', 1)
ON CONFLICT (id) DO NOTHING;
```

### New table: `bot_state_events`

Append-only state transition/audit log.

```sql
CREATE TABLE IF NOT EXISTS bot_state_events (
  id UUID PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  actor_type VARCHAR(32) NOT NULL,
  actor_id VARCHAR(128),
  source VARCHAR(64) NOT NULL,
  from_state VARCHAR(32),
  to_state VARCHAR(32) NOT NULL,
  reason VARCHAR(64),
  detail JSONB,
  run_id VARCHAR(64),
  session_id VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_bot_state_events_created_at ON bot_state_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_state_events_run_id ON bot_state_events(run_id);
```

### State enum (logical)

- `RUNNING`
- `PAUSED_MANUAL`
- `PAUSED_RISK`
- `PAUSED_SYSTEM`

### Important rule

`settings.bot_enabled` is deprecated for control flow.
During migration, mirror values for backward compatibility only.

---

## 3) Transition Rules

Single transition function (API + worker both use same code path):

`transition_bot_state(target_state, reason, source, detail, actor)`

Rules:

1. Manual pause/resume always writes `desired_state`.
2. Worker computes `effective_state` each cycle from:
   - `desired_state`
   - hard safety guards (risk/system errors)
3. Worker never mutates manual intent directly.
4. Every transition writes one `bot_state_events` record.
5. Optimistic lock via `version` to avoid race clobbering.

---

## 4) Session-Based Risk Window

On resume, mint `session_id` (UTC timestamp + random suffix).

Daily-loss/risk checks scope by:

- `run_id == active_run_id`
- `trade.session_id == current session_id`

If needed for compatibility, backfill `session_id` nullable and set only for new trades.

This removes ambiguity around `created_at`/`resolved_at` when old positions close later.

---

## 5) API Contract

### `GET /controls/state`

Returns effective and desired state with reason and recent event.

```json
{
  "desired_state": "RUNNING",
  "effective_state": "PAUSED_RISK",
  "pause_reason": "daily_loss_limit",
  "pause_detail": "session loss -120 exceeds limit -100",
  "active_run_id": "paper-v2-20260303-070247",
  "session_id": "sess-20260303-182154-ab12",
  "last_transition_at": "2026-03-03T18:22:34Z",
  "updated_by": "worker",
  "version": 27,
  "env": {
    "bot_enabled_env": true,
    "enable_llm": false
  }
}
```

### `POST /controls/pause`

Request:

```json
{ "reason": "manual_pause" }
```

Behavior:

- Set `desired_state = PAUSED_MANUAL`
- Set `effective_state = PAUSED_MANUAL`
- Record event.

### `POST /controls/resume`

Request:

```json
{ "reason": "manual_resume", "new_session": true }
```

Behavior:

- Set `desired_state = RUNNING`
- Mint `session_id` when `new_session=true`
- Clear pause reason/detail
- Set `effective_state = RUNNING` unless immediate hard guard fails
- Record event.

### `GET /controls/state/events?limit=50`

Returns latest transition events for audit/debug.

---

## 6) Worker Loop Contract

Per scan cycle:

1. Read `bot_state` once.
2. If `desired_state != RUNNING`, skip with structured log.
3. Evaluate risk/system guards.
4. If guard trips:
   - `effective_state = PAUSED_RISK` or `PAUSED_SYSTEM`
   - keep `desired_state` unchanged
   - write event with reason/detail
   - skip trading.
5. If no guard trip and desired is `RUNNING`, set `effective_state = RUNNING`.

No direct writes to `settings.bot_enabled` by scanner/executor.

---

## 7) LLM Isolation

- Keep `ENABLE_LLM=false` default.
- Reflection generation runs in separate scheduled task/worker.
- Trading loop should never await or depend on LLM calls.

---

## 8) Rollout Plan

### Phase A (safe migration)

1. Add new tables + transition utility.
2. Introduce `/controls/state` and `/controls/state/events` on new model.
3. Keep old `settings.bot_enabled` mirrored for compatibility.

### Phase B (cutover)

1. Update worker loop to read/write `bot_state` only.
2. Update pause/resume endpoints to use transitions only.
3. Remove scanner writes to legacy pause settings.

### Phase C (topology split)

1. Deploy separate Railway services.
2. Remove multi-process `entrypoint.sh` for production.
3. Add worker health probe + alerting.

### Phase D (cleanup)

1. Remove `settings.bot_enabled` control usage entirely.
2. Keep one-way compatibility shim for UI until frontend fully migrated.

---

## 9) Minimum Test Matrix

1. Resume -> state RUNNING -> scans execute.
2. Manual pause -> scans skip -> remains paused.
3. Risk pause -> effective paused, desired remains RUNNING.
4. Resume after risk pause starts new session.
5. Worker DB error triggers `PAUSED_SYSTEM` event but API stays up.
6. Dual concurrent resume/pause requests respect `version` lock semantics.

---

## 10) Definition of Done

- No unexplained pause loops.
- Any paused state includes machine-readable reason and source.
- API and worker can restart independently.
- Trading remains functional with LLM disabled.

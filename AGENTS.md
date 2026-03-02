# AGENTS.md — Agent Coding Guidelines for Kalshi Bot

This file provides coding guidelines and commands for AI agents working in this repository.

---

## Project Overview

A real-money trading bot for [Kalshi](https://kalshi.com) prediction markets with:
- **Python 3.11+** backend using asyncio, SQLAlchemy async, FastAPI
- **Next.js 14** mobile-first PWA dashboard
- **PostgreSQL** (Supabase) database
- **APScheduler** for scheduled bot tasks

---

## Commands

### Python Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python -m bot.main

# Run the API server (development)
uvicorn api.main:app --reload --port 8000

# Run the API server (production)
uvicorn api.main:app --host 0.0.0.0 --port $PORT

# Run tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_param_guardrails.py -v

# Run a single test
python -m pytest tests/test_param_guardrails.py::TestValidateProposedValue::test_valid_float -v
```

### Frontend (UI)

```bash
cd ui

# Install dependencies
npm install

# Run development server
npm run dev

# Build for production
npm run build

# Run linter
npm run lint
```

---

## Code Style — Python

### Imports

- Standard library first, then third-party, then local
- Always use explicit relative imports for local modules:
  ```python
  from bot.core.kalshi_client import KalshiClient
  from api.models import Base
  ```
- Never use `from module import *`

### Type Hints

- Use type hints on all function signatures
- Use `Optional[X]` instead of `X | None`
- Use built-in generics: `list[str]`, `dict[str, int]`
- Use `from typing import Optional` for Python < 3.9 compatibility

### Naming

- **Classes**: `PascalCase` (e.g., `KalshiClient`, `RiskManager`)
- **Functions/variables**: `snake_case` (e.g., `check_trade`, `open_positions`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `KALSHI_FEE_PER_CONTRACT`)
- **Private methods**: prefix with `_` (e.g., `_load_private_key`)

### Dataclasses

Use `@dataclass` for simple data containers:
```python
from dataclasses import dataclass

@dataclass
class TradeDecision:
    approved: bool
    recommended_size: int
    reason: str
    kelly_fraction: float = 0.0
```

### Error Handling

- Define custom exceptions for domain errors:
  ```python
  class KalshiAPIError(Exception):
      def __init__(self, status_code: int, message: str):
          self.status_code = status_code
          self.message = message
          super().__init__(f"Kalshi API error {status_code}: {message}")
  ```
- Use `tenacity` for retry logic (decorator-based)
- Never swallow exceptions silently — log and re-raise or handle explicitly

### Logging

- Use Python's `logging` module, not print statements
- Get logger at module level: `logger = logging.getLogger(__name__)`
- Log levels: `DEBUG` (every API call), `INFO` (summaries), `WARNING` (rule triggers), `ERROR` (failures), `CRITICAL` (unrecoverable)
- Format: `[TIMESTAMP] [LEVEL] [MODULE] Message`

### Async/Await

- Never use synchronous `requests` — always use `httpx.AsyncClient`
- Never use synchronous SQLAlchemy — always use `sqlalchemy[asyncio]` + `asyncpg`
- Use `async with` for context managers that support async

### Configuration

- All configurable values via environment variables or `settings` table
- Never hardcode API keys, secrets, or thresholds
- Use `os.getenv("KEY", default)` with sensible defaults

---

## Code Style — TypeScript/React

### Component Structure

- Use functional components with hooks
- Place types in same file or in `lib/types.ts` for shared types
- Use `"use client"` directive for client-side components

### Naming

- **Components**: `PascalCase` (e.g., `StatCard.tsx`)
- **Functions/variables**: `camelCase`
- **Files**: `kebab-case.ts` or `PascalCase.tsx` for components

### TypeScript

- Always define interfaces for API responses
- Use `export interface` for reusable types
- Avoid `any` — use `unknown` if type is uncertain

### Styling

- Use Tailwind CSS exclusively
- Dark mode only — colors from `tailwind.config.ts`:
  - Background: `#0a0a0a`
  - Card: `#1a1a1a`
  - Border: `#2a2a2a`
  - Green: `#00d4aa` (positive)
  - Red: `#ff4444` (negative/paused)
  - Orange: `#ff8c00` (warning)

### React Patterns

- Use `useCallback` for functions passed to child components
- Use `useEffect` with cleanup (return cleanup function)
- Prefer `Promise.all` for parallel async calls

---

## Testing Guidelines

### Python (pytest)

- Test files in `tests/` directory
- Name: `test_*.py`
- Use class-based tests for organization:
  ```python
  class TestValidateProposedValue:
      def test_valid_float(self):
          ok, err = validate_proposed_value("bond_stop_loss_cents", 0.05)
          assert ok
  ```
- Run single test: `python -m pytest path/to/test.py::ClassName::test_name -v`

### Test Philosophy

- Test behavior, not implementation
- Keep tests independent and idempotent
- Mock external APIs (Kalshi, LLM) in unit tests

---

## Database

- Use async SQLAlchemy 2.0 with `asyncpg` driver
- Models in `api/models.py`
- Session management via `api/database.py`
- Never cache settings in memory — read from DB every cycle

---

## API Design

### Backend (FastAPI)

- All routes require Bearer token auth
- Use Pydantic models for request/response validation
- Open CORS for PWA (`allow_origins=["*"]`)
- Health check at `/health`

### Frontend API Client

- Single `apiFetch` wrapper in `ui/lib/api.ts`
- Bearer token auth header
- Type-safe response interfaces

---

## Safety Rules (Non-Negotiable)

1. **Never bypass RiskManager** — every trade must pass `check_trade()`
2. **Never log secrets** — never log API keys, passwords, or credentials
3. **Never commit secrets** — use `.env` files, not hardcoded credentials
4. **Fail loudly** — prefer exceptions over silent failures
5. **Idempotent operations** — scanner can run multiple times without side effects

---

## File Locations

| Purpose | Path |
|---------|------|
| Bot entry | `bot/main.py` |
| API entry | `api/main.py` |
| UI entry | `ui/app/page.tsx` |
| DB models | `api/models.py` |
| DB schema | `db/schema.sql` |
| Environment template | `.env.example` |
| Tests | `tests/` |

---

## Key Dependencies

### Python
- `fastapi`, `uvicorn` — API server
- `sqlalchemy[asyncio]`, `asyncpg` — Async DB
- `httpx` — Async HTTP client
- `anthropic` — LLM API client
- `apscheduler` — Job scheduling
- `tenacity` — Retry logic
- `pytest` — Testing

### JavaScript
- `next` 14 — React framework
- `react` — UI library
- `recharts` — Charts
- `tailwindcss` — Styling

---

## Important Notes

- This bot trades real money — be conservative with changes
- Always run tests before committing
- Check CLAUDE.md for additional context and architecture decisions
- The `settings` table in DB is the source of truth for runtime config

# CLAUDE.md — Kalshi Prediction Market Trading Bot

This file tells Claude Code everything it needs to know about this project to assist effectively. Read this before touching any code.

-----

## What This Project Is

An autonomous trading bot for **Kalshi** (a CFTC-regulated US prediction market). It scans for profitable opportunities, executes trades, manages risk, reflects on outcomes using AI, and exposes a mobile-first PWA dashboard for iPhone monitoring and control.

The bot must be safe, reliable, and autonomous. The owner has limited time. Every architectural decision should favor **simplicity and stability** over cleverness.

-----

## Project Structure

```
kalshi-bot/
├── bot/                    # Core trading engine (Python)
│   ├── strategies/         # Bond, market making, news arbitrage
│   ├── core/               # Kalshi client, risk manager, scanner, executor
│   ├── intelligence/       # News listener, signal scorer, reflection engine
│   └── main.py             # Entry point + APScheduler
├── api/                    # FastAPI REST backend
│   ├── routes/             # dashboard, trades, positions, reflections, controls
│   ├── models.py           # SQLAlchemy ORM models
│   ├── database.py         # Async DB session setup
│   └── main.py             # FastAPI app
├── ui/                     # Next.js 14 PWA (iPhone dashboard)
│   ├── app/                # App Router pages
│   └── components/         # Reusable UI components
├── db/
│   └── schema.sql          # PostgreSQL schema
├── scripts/
│   └── backtest.py         # Historical backtesting
├── BUILD_PLAN.md           # Master build plan with all session prompts
├── .env.example            # All required env vars
└── requirements.txt
```

-----

## Development Principles

### Safety First

This bot controls real money. When in doubt, **be conservative**:

- Never place a trade without passing through RiskManager first
- Always check `bot_enabled` setting before executing
- Log every decision with enough context to debug later
- Fail loudly (raise exceptions) rather than silently continuing

### Async Throughout

The entire Python stack uses `async/await`. Never use `requests` — always `httpx.AsyncClient`. Never use synchronous SQLAlchemy — always use `asyncpg` + `sqlalchemy[asyncio]`.

### No Magic Numbers

All configurable values (position size %, loss limits, price thresholds, timing windows) must come from environment variables or the `settings` database table — never hardcoded.

### Idempotency

The scanner runs every 60 seconds. It must be idempotent — running it twice should not create duplicate trades. Always check for existing open positions/orders before creating new ones.

-----

## Technology Decisions (Don’t Change These Without Good Reason)

|Decision              |Choice                       |Reason                                   |
|----------------------|-----------------------------|-----------------------------------------|
|Python async framework|asyncio + APScheduler        |Simple, no external broker needed        |
|HTTP client           |httpx.AsyncClient            |Native async, good retry support         |
|ORM                   |SQLAlchemy 2.0 async         |Type-safe, async-native                  |
|Database              |PostgreSQL (Supabase)        |Reliable, good free tier, easy to inspect|
|Retry logic           |tenacity                     |Clean decorator-based retries            |
|AI                    |Anthropic claude-opus-4-6                |Reflections + recommendations; set ANTHROPIC_API_KEY             |
|UI framework          |Next.js 14 App Router        |PWA support, easy Vercel deploy          |
|UI styling            |Tailwind CSS (dark mode only)|Fast, mobile-first                       |
|Bot hosting           |Railway                      |Supports long-running processes, simple  |
|UI hosting            |Vercel                       |Zero-config Next.js deploys              |

-----

## Key Files and Their Roles

### `bot/core/kalshi_client.py`

The only file that talks to Kalshi’s API. All API calls go through this. It handles authentication (HMAC signing), rate limiting, retries, and logging. Never call Kalshi’s API directly from strategy files.

### `bot/core/risk_manager.py`

Every trade must be approved by `RiskManager.check_trade()` before execution. This is non-negotiable. It enforces position limits, daily loss limits, Kelly sizing, and correlation rules.

### `bot/intelligence/signal_scorer.py`

The `TradeSignal` dataclass is the universal currency of this system. Every strategy returns `list[TradeSignal]`. The scorer ranks and filters them. The executor consumes them.

### `bot/core/executor.py`

The only file that places real orders and writes to the `trades` and `positions` tables. Keep it simple — no strategy logic here.

### `bot/intelligence/reflection_engine.py`

Calls Claude API after every resolved trade. Writes to the `reflections` table. This is the “learning” system. It runs asynchronously and should never block trade execution.

### `api/main.py`

FastAPI app. All routes require Bearer token auth. CORS is open (PWA needs it). Connects to the same PostgreSQL database as the bot.

### `bot/main.py`

The entry point. Starts APScheduler, wires all components together, handles graceful shutdown. This is what Railway runs.

-----

## Database Tables

|Table               |Purpose                                            |
|--------------------|---------------------------------------------------|
|`trades`            |Every trade ever placed, including outcome and PnL |
|`positions`         |Currently open positions (deleted when closed)     |
|`reflections`       |AI-generated post-mortems per trade                |
|`weekly_reflections`|AI-generated weekly summary reports                |
|`settings`          |Runtime config (bot_enabled, position limits, etc.)|

**Important:** The `settings` table is the control plane. The UI writes to it, the bot reads from it. Always use async DB reads for settings inside the bot — never cache them in memory for more than one scan cycle.

-----

## Trading Strategies

### Bond Strategy

- **What:** Buy near-certain outcomes (priced 94¢+) before resolution
- **Risk:** Low. Main risk is surprise black swan events.
- **Scan frequency:** Every 60 seconds
- **Key parameter:** `BOND_MIN_PRICE=0.94` — don’t lower this without careful thought

### Market Making

- **What:** Place limit orders on both sides of liquid markets, earn the spread
- **Risk:** Inventory risk — if one side fills repeatedly without the other, we get directional exposure
- **Key check:** If one side is > 60% filled without the other, cancel both and reset
- **Key parameter:** Only enter markets with spread > 0.04 (after Kalshi’s fees)

### BTC 15-Minute Strategy

- **What:** Trade Kalshi Bitcoin price prediction markets using a lognormal probability model against the current BTC spot price
- **Risk:** Medium. Model assumes constant volatility (4% daily). Sudden BTC moves will cause losses.
- **Price feed:** CoinGecko free API (no key needed). Caches last price on failure.
- **Key parameters:** Only enter markets within 4 hours of resolution with ≥ 4% edge. Min volume $5k.
- **Key file:** `bot/strategies/btc_strategy.py`

-----

## Risk Rules (Hard Limits)

These must NEVER be bypassed, even if a trade looks obviously profitable:

1. **Max single position:** 15% of bankroll (configurable, never exceed 20%)
1. **Max total exposure:** 60% of bankroll in open positions
1. **Daily loss limit:** 3% of bankroll — bot pauses for the rest of the day if hit
1. **Minimum edge:** Only trade if `our_probability - market_price > 0.02`
1. **Minimum liquidity:** Never trade markets with volume under $5,000
1. **Correlation limit:** Max 2 positions in the same market category within 48 hours

-----

## Environment Variables

See `.env.example` for all variables. Critical ones:

- `KALSHI_API_KEY` / `KALSHI_API_SECRET` — Kalshi credentials
- `DATABASE_URL` — PostgreSQL connection string (asyncpg format)
- `MINIMAX_CODING_PLAN_API_KEY` or `ANTHROPIC_API_KEY` — LLM for reflections and news classification (use one)
- `API_BEARER_TOKEN` — Secret token for PWA ↔ API auth
- `BOT_ENABLED` — Master on/off switch (also controlled via DB settings table)
- `INITIAL_BANKROLL` — Starting capital amount

-----

## Common Patterns

### Async DB Session in Bot

```python
async with async_session() as session:
    result = await session.execute(select(Trade).where(Trade.status == 'open'))
    trades = result.scalars().all()
```

### Getting Settings

```python
async def get_setting(session, key: str, default: str = None) -> str:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else default
```

### Calling the LLM (MiniMax or Anthropic)

Reflection engine and news listener use the Anthropic-compatible API. If `MINIMAX_CODING_PLAN_API_KEY` is set, MiniMax is used; else Anthropic.

```python
import anthropic
# MiniMax (Plus High Speed): base_url="https://api.minimaxi.com/anthropic", model="minimax-m2.5-highspeed"
# Anthropic: default base_url, model="claude-haiku-4-5"
client = anthropic.AsyncAnthropic(api_key=os.getenv("MINIMAX_CODING_PLAN_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))
response = await client.messages.create(
    model="minimax-m2.5-highspeed",  # or "claude-haiku-4-5"
    max_tokens=500,
    messages=[{"role": "user", "content": prompt}]
)
# Prefer first text block (MiniMax may return thinking + text)
text = next((b.text for b in response.content if hasattr(b, "text")), response.content[0].text)
result = json.loads(text)
```

### Kalshi Price Format

Kalshi prices are integers 0–100 (cents). Internally we use floats 0.0–1.0. Convert at the API boundary:

- Sending to API: `int(price * 100)`
- Receiving from API: `price_int / 100`

-----

## Error Handling Philosophy

- **Network errors:** Retry with exponential backoff (tenacity handles this in KalshiClient)
- **API 429 (rate limit):** Wait and retry — never crash
- **Strategy errors:** Log the error, skip that signal, continue scanning
- **Executor errors:** Log, don’t retry automatically (avoid duplicate orders), alert via log ERROR
- **Database errors:** These are serious — log CRITICAL and skip the cycle
- **Claude API errors:** Non-blocking — if reflection fails, log it and move on. Don’t let it affect trading.

-----

## Logging Standards

Use Python’s `logging` module throughout. Format:

```
[TIMESTAMP] [LEVEL] [MODULE] Message
```

Log levels:

- `DEBUG`: Every API call, every signal scored
- `INFO`: Scan cycle summary, trades placed, positions closed
- `WARNING`: Risk rule triggered, strategy skipped, daily loss limit approaching
- `ERROR`: Failed order placement, DB errors, unexpected API responses
- `CRITICAL`: Daily loss limit hit, bot pausing, unrecoverable errors

-----

## UI Design Rules

- **Dark mode only:** Background `#0a0a0a`, cards `#1a1a1a`, borders `#2a2a2a`
- **Color semantics:** Green `#00d4aa` = positive/active, Red `#ff4444` = negative/paused, Orange `#ff8c00` = warning/neutral
- **Mobile-first:** Design for 390px wide (iPhone 14 Pro). No horizontal scroll.
- **Bottom navigation:** Always visible, 4 tabs: Dashboard | Trades | Reflections | Controls
- **No tables:** Use card lists for all data displays
- **Auto-refresh:** Dashboard refreshes every 30 seconds automatically
- **Loading states:** Always show skeletons, never blank screens

-----

## When Helping With This Project

1. **Always check for existing implementations** before creating new files
1. **Never remove safety checks** — risk management is sacred
1. **Keep the bot and API loosely coupled** — they share only the database
1. **Test database operations carefully** — production data is real money
1. **Prefer simple solutions** — this runs 24/7 unattended, complexity = bugs
1. **When modifying strategies**, explain the expected PnL impact
1. **Never hardcode API keys** or credentials anywhere in code
1. **The `settings` table overrides env vars** for runtime behavior — the DB is the source of truth at runtime

-----

## Current Development Status

Track progress here as sessions are completed:

- [x] Session 1 — Project scaffold
- [x] Session 2 — Kalshi API client
- [x] Session 3 — Risk manager
- [x] Session 4 — Signal scorer
- [x] Session 5 — Bond strategy
- [x] Session 6 — Market making strategy
- [x] Session 7 — News listener
- [x] Session 8 — News arbitrage strategy
- [x] Session 9 — Scanner + Executor
- [x] Session 10 — Reflection engine
- [x] Session 11 — FastAPI backend
- [x] Session 12 — Bot main entry point
- [x] Session 13 — Next.js PWA UI
- [x] Session 14 — Deployment config
- [x] Post-14 — Removed news arbitrage, added BTC 15-min strategy, improved MiniMax LLM startup validation

-----

## Disclaimers

- This bot trades real money on real markets. Past performance of any strategy does not guarantee future results.
- Prediction markets carry inherent risk. The bond strategy can lose on black swan events.
- Always start with paper trading or small amounts while validating bot behavior.
- Monitor the bot daily for the first two weeks before trusting full autonomy.
- Never put money into this bot that you cannot afford to lose entirely.

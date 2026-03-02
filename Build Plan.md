# Kalshi Prediction Market Trading Bot — Build Plan

## Project Overview

An autonomous trading bot for Kalshi (US-legal prediction market), with a self-reflection AI engine, risk management, and a mobile-first PWA dashboard accessible from iPhone.

**Owner:** Solo developer, US-based  
**Starting Capital:** Under $5,000  
**Goal:** Maximize and compound gains consistently using automated strategies while requiring minimal daily attention.

-----

## Core Strategies (v1)

1. **Bond Strategy** — Trade markets priced at 94¢+ with resolution within 72 hours. Earn 2–6% per trade with low risk.
1. **Market Making / LP** — Place limit orders on both YES and NO sides of liquid markets, earn the bid-ask spread.
1. **News-Driven Arbitrage** — Monitor RSS/news feeds, enter markets within 30–300 seconds of a relevant headline before prices adjust.

-----

## Tech Stack

|Layer          |Technology                             |
|---------------|---------------------------------------|
|Bot Engine     |Python 3.11+                           |
|API Backend    |FastAPI + Uvicorn                      |
|Database       |PostgreSQL (Supabase free tier)        |
|UI             |Next.js 14 PWA (mobile-first)          |
|Bot/API Hosting|Railway.app                            |
|UI Hosting     |Vercel                                 |
|AI Reflection  |Anthropic Claude API (claude-haiku-4-5)|
|News Feed      |GNews API + RSS parsing                |
|Scheduler      |APScheduler (in-process)               |
|Auth           |Simple Bearer token in .env            |

-----

## Repository Structure

```
kalshi-bot/
├── bot/
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── bond_strategy.py
│   │   ├── market_making.py
│   │   └── news_arbitrage.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── kalshi_client.py
│   │   ├── risk_manager.py
│   │   ├── scanner.py
│   │   └── executor.py
│   ├── intelligence/
│   │   ├── __init__.py
│   │   ├── reflection_engine.py
│   │   ├── news_listener.py
│   │   └── signal_scorer.py
│   └── main.py
├── api/
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py
│   │   ├── trades.py
│   │   ├── positions.py
│   │   ├── reflections.py
│   │   └── controls.py
│   ├── __init__.py
│   ├── models.py
│   ├── database.py
│   └── main.py
├── ui/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   ├── trades/page.tsx
│   │   ├── reflections/page.tsx
│   │   └── controls/page.tsx
│   ├── components/
│   │   ├── StatCard.tsx
│   │   ├── TradeCard.tsx
│   │   ├── ReflectionCard.tsx
│   │   ├── PositionCard.tsx
│   │   └── KillSwitch.tsx
│   ├── lib/
│   │   └── api.ts
│   ├── public/
│   │   └── manifest.json
│   ├── next.config.js
│   ├── tailwind.config.ts
│   └── package.json
├── db/
│   └── schema.sql
├── scripts/
│   └── backtest.py
├── docs/
│   └── architecture.md
├── .env.example
├── .gitignore
├── requirements.txt
├── Dockerfile
├── railway.toml
├── CLAUDE.md
└── README.md
```

-----

## Environment Variables (.env.example)

```
# Kalshi API
KALSHI_API_KEY=your_kalshi_api_key
KALSHI_API_SECRET=your_kalshi_api_secret
KALSHI_BASE_URL=https://api.elections.kalshi.com/trade-api/v2

# Database
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/kalshi_bot

# Anthropic (for reflection engine)
ANTHROPIC_API_KEY=your_anthropic_api_key

# News
GNEWS_API_KEY=your_gnews_api_key

# App Security
API_BEARER_TOKEN=generate_a_random_secret_string_here

# Bot Settings
INITIAL_BANKROLL=5000
MAX_POSITION_PCT=0.15
DAILY_LOSS_LIMIT_PCT=0.03
BOT_ENABLED=true
ENVIRONMENT=production
```

-----

## Database Schema (db/schema.sql)

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market_id VARCHAR(255) NOT NULL,
    market_title TEXT NOT NULL,
    strategy VARCHAR(50) NOT NULL CHECK (strategy IN ('bond', 'market_making', 'news_arbitrage')),
    side VARCHAR(10) NOT NULL CHECK (side IN ('yes', 'no')),
    size INTEGER NOT NULL,
    entry_price DECIMAL(6,4) NOT NULL,
    exit_price DECIMAL(6,4),
    gross_pnl DECIMAL(10,2),
    fees DECIMAL(10,2),
    net_pnl DECIMAL(10,2),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
    entry_reasoning TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE positions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market_id VARCHAR(255) NOT NULL UNIQUE,
    market_title TEXT NOT NULL,
    strategy VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,
    size INTEGER NOT NULL,
    entry_price DECIMAL(6,4) NOT NULL,
    current_price DECIMAL(6,4),
    unrealized_pnl DECIMAL(10,2),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE TABLE reflections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trade_id UUID REFERENCES trades(id),
    summary TEXT NOT NULL,
    what_worked TEXT,
    what_failed TEXT,
    confidence_score INTEGER CHECK (confidence_score BETWEEN 1 AND 10),
    strategy_suggestion TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE weekly_reflections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    total_trades INTEGER,
    win_rate DECIMAL(5,2),
    net_pnl DECIMAL(10,2),
    top_strategy VARCHAR(50),
    summary TEXT,
    key_learnings TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default settings
INSERT INTO settings (key, value) VALUES
    ('bot_enabled', 'true'),
    ('bond_strategy_enabled', 'true'),
    ('market_making_enabled', 'true'),
    ('news_arbitrage_enabled', 'true'),
    ('max_position_pct', '0.15'),
    ('daily_loss_limit_pct', '0.03'),
    ('current_bankroll', '5000');

CREATE INDEX idx_trades_created_at ON trades(created_at DESC);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_strategy ON trades(strategy);
CREATE INDEX idx_reflections_trade_id ON reflections(trade_id);
CREATE INDEX idx_reflections_created_at ON reflections(created_at DESC);
```

-----

## Build Sessions — Execute In Order

Each session below is a self-contained Claude Code prompt. Complete and test each before proceeding to the next.

-----

### SESSION 1 — Project Scaffold

**Prompt:**

```
Set up a Python project called kalshi-bot with the following:

1. Create the full folder/file structure as defined in the BUILD_PLAN.md repo structure. Create empty __init__.py files in all Python package directories.

2. Create requirements.txt with these packages:
fastapi==0.109.0
uvicorn[standard]==0.27.0
sqlalchemy[asyncio]==2.0.25
asyncpg==0.29.0
httpx==0.26.0
apscheduler==3.10.4
anthropic==0.18.1
python-dotenv==1.0.0
pydantic==2.5.3
pydantic-settings==2.1.0
feedparser==6.0.11
aiohttp==3.9.1
tenacity==8.2.3

3. Create .env.example with all variables from BUILD_PLAN.md.

4. Create .gitignore that ignores: .env, __pycache__, .pytest_cache, node_modules, .next, *.pyc, venv/, .DS_Store

5. Create db/schema.sql with the full schema from BUILD_PLAN.md.

6. Create a basic README.md with: project description, prerequisites (Python 3.11+, Node 18+, Supabase account, Kalshi account, Anthropic API key), and placeholder sections for Setup and Deployment.
```

-----

### SESSION 2 — Kalshi API Client

**Prompt:**

```
Build bot/core/kalshi_client.py — a fully async Python client for the Kalshi REST API v2.

Requirements:
- Load credentials from environment variables (KALSHI_API_KEY, KALSHI_API_SECRET, KALSHI_BASE_URL)
- Authentication: Kalshi uses API key + secret with HMAC-SHA256 request signing. Sign: timestamp + method + path. Include headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE
- Use httpx.AsyncClient with connection pooling
- All methods must be async
- Implement retry logic with exponential backoff using tenacity (3 retries, wait 2^x seconds)
- Handle rate limiting: if 429 received, wait and retry
- Log all requests and responses at DEBUG level using Python logging

Methods to implement:
- get_balance() -> dict  (returns available USDC balance)
- get_markets(status='open', category=None, limit=100) -> list[dict]  (paginate if needed)
- get_market(ticker: str) -> dict
- get_orderbook(ticker: str) -> dict  (returns yes/no bids and asks)
- place_order(ticker: str, side: str, count: int, price: int, order_type: str = 'limit') -> dict
  (price is in cents 0-100, count is number of contracts)
- cancel_order(order_id: str) -> dict
- get_orders(status: str = 'open') -> list[dict]
- get_positions() -> list[dict]
- get_fills(ticker: str = None) -> list[dict]

Create a KalshiClient class. Initialize with a context manager (async with KalshiClient() as client).
Include a simple test at the bottom under if __name__ == '__main__' that prints the balance.
```

-----

### SESSION 3 — Risk Manager

**Prompt:**

```
Build bot/core/risk_manager.py.

Create a RiskManager class that enforces all trading safety rules.

Rules to enforce:
1. Max single position size: configurable, default 15% of current bankroll
2. Max total exposure: no more than 60% of bankroll in open positions simultaneously
3. Daily loss limit: if daily PnL drops below -3% of bankroll, set a pause flag and stop all trading
4. No more than 2 positions in the same Kalshi market category within 48 hours (correlation risk)
5. Minimum market liquidity: reject trades on markets with volume under $5,000
6. Kelly Criterion sizing: given edge (our_prob - market_prob) and odds, calculate optimal bet size; use half-Kelly for safety

Implement these methods:
- async check_trade(market: dict, side: str, proposed_size: int, bankroll: float, open_positions: list) -> TradeDecision
  Returns a TradeDecision dataclass with: approved (bool), recommended_size (int), reason (str)
- calculate_kelly_size(our_probability: float, market_price: float, bankroll: float) -> int
  Returns number of contracts (each contract = $0.01 * price)
- async check_daily_loss_limit(db_session) -> bool
  Queries today's closed trades, returns True if limit hit
- get_max_position_size(bankroll: float) -> float

TradeDecision dataclass:
  approved: bool
  recommended_size: int  
  reason: str
  kelly_fraction: float

Load max_position_pct and daily_loss_limit_pct from environment variables with fallback defaults.
Include logging for every rejection with reason.
```

-----

### SESSION 4 — Signal Scorer

**Prompt:**

```
Build bot/intelligence/signal_scorer.py.

This module scores and ranks trade opportunities by expected value and risk-adjusted return.

Create a TradeSignal dataclass:
  ticker: str
  market_title: str
  strategy: str  ('bond', 'market_making', 'news_arbitrage')
  side: str  ('yes' or 'no')
  proposed_size: int
  entry_price: float  (0.0 to 1.0)
  our_probability: float  (our estimated true probability)
  expected_value: float  (edge = our_prob - market_price)
  expected_return_pct: float  (profit / cost if win)
  time_to_resolution: float  (hours)
  annualized_return: float  (expected_return_pct extrapolated to annual)
  confidence: float  (0.0 to 1.0)
  reasoning: str
  news_headline: str = None  (for news_arbitrage signals)

Create a SignalScorer class with:
- score_signal(signal: TradeSignal) -> float  
  Composite score = (expected_value * 0.4) + (annualized_return_normalized * 0.3) + (confidence * 0.3)
  Penalize signals with time_to_resolution > 48 hours by 20%
  
- rank_signals(signals: list[TradeSignal]) -> list[TradeSignal]  
  Sort by score descending, remove duplicates by ticker
  
- filter_minimum_edge(signals: list[TradeSignal], min_edge: float = 0.02) -> list[TradeSignal]
  Remove any signal where expected_value < min_edge
```

-----

### SESSION 5 — Bond Strategy

**Prompt:**

```
Build bot/strategies/bond_strategy.py.

This strategy scans for near-certain Kalshi markets to trade.

Create a BondStrategy class with:
- async scan(client: KalshiClient) -> list[TradeSignal]

Scanning logic:
1. Fetch all open markets from Kalshi API
2. For each market, fetch the orderbook
3. Filter for markets where:
   - Best YES ask price is <= 0.06 (meaning NO is >= 0.94), OR best NO ask price is <= 0.06 (meaning YES is >= 0.94)
   - Market closes within 72 hours
   - Total volume > $5,000
   - We don't already have a position in this market
4. Calculate expected return: if buying NO at $0.06, we pay $0.06 and receive $1.00 if correct = 1567% return, but probability-adjusted
5. For each qualifying market, create a TradeSignal:
   - side: whichever of YES/NO is priced >= 0.94
   - entry_price: best ask for that side
   - our_probability: 0.97 (assume the market is right, slight discount for black swan)
   - expected_value: our_probability - entry_price
   - expected_return_pct: (1.0 - entry_price) / entry_price  (profit if win / cost)
   - reasoning: f"Bond play: {side} at {price:.2f} with {hours:.1f}h to resolution"
   - confidence: 0.85

Return list of TradeSignal objects, sorted by expected_return_pct descending.
Import TradeSignal from bot.intelligence.signal_scorer.
```

-----

### SESSION 6 — Market Making Strategy

**Prompt:**

```
Build bot/strategies/market_making.py.

This strategy provides liquidity by placing limit orders on both sides of markets and earning the spread.

Create a MarketMakingStrategy class with:

- async scan(client: KalshiClient, open_orders: list) -> list[TradeSignal]
  
  Scanning logic:
  1. Fetch open markets with decent volume (> $10,000)
  2. For each market fetch the orderbook
  3. Calculate current spread: best_no_ask - best_yes_ask (in a binary market, yes_price + no_price should = ~1.00)
  4. Target markets where spread > 0.04 (4 cents) — enough room after Kalshi's fees (~0.07/contract)
  5. Skip markets where we already have open MM orders
  6. Skip markets resolving in < 4 hours (too risky)
  7. For qualifying markets, generate TWO signals: one for YES side, one for NO side
     - YES order: price = best_yes_bid + 0.01 (penny inside best bid)
     - NO order: price = best_no_bid + 0.01
     - Size: small (10-20 contracts) to limit inventory risk
     - reasoning: f"Market making: placing {side} at {price:.2f}, spread is {spread:.3f}"
     - strategy: 'market_making'
     - confidence: 0.70

- async manage_inventory(client: KalshiClient, positions: list) -> list
  Check if any MM position has become too one-sided (one side filled > 60% without other side).
  Return list of order IDs to cancel if rebalancing needed.

Import TradeSignal from bot.intelligence.signal_scorer.
```

-----

### SESSION 7 — News Listener

**Prompt:**

```
Build bot/intelligence/news_listener.py.

This module monitors news feeds and classifies headlines for trading relevance.

Create a NewsListener class:

RSS feeds to monitor (hardcode these):
- https://feeds.reuters.com/reuters/topNews
- https://rss.app/feeds/politics.xml  (AP Politics)  
- https://feeds.feedburner.com/coindesk/CoinDeskMain
- https://rss.politico.com/politics-news.xml
- https://feeds.bloomberg.com/markets/news.rss

Methods:
- async start_polling(callback: callable, interval_seconds: int = 30)
  Poll all feeds every interval_seconds. For each new headline not seen before, call callback(headline_dict).
  Track seen headlines by guid/link to avoid duplicates. Store seen set in memory.

- async fetch_feed(url: str) -> list[dict]
  Use feedparser to fetch and parse RSS. Return list of dicts with: title, summary, link, published, source.
  Handle fetch errors gracefully — log and continue.

- async classify_headline(headline: dict) -> ClassifiedHeadline
  Call Claude API (claude-haiku-4-5) with this system prompt:
  "You are a prediction market trading assistant. Classify news headlines for their impact on Kalshi prediction markets. Return JSON only."
  
  User prompt:
  "Headline: {title}\nSummary: {summary}\n\nReturn JSON: {\"relevant\": bool, \"affected_categories\": [list of Kalshi categories like 'politics', 'economics', 'crypto', 'sports'], \"direction\": \"yes_up\" or \"no_up\" or \"neutral\", \"confidence\": 0.0-1.0, \"reasoning\": \"one sentence\"}"
  
  Parse JSON response. Return ClassifiedHeadline dataclass.

ClassifiedHeadline dataclass:
  headline: str
  summary: str
  source: str
  published: datetime
  relevant: bool
  affected_categories: list[str]
  direction: str
  confidence: float
  reasoning: str

Use the anthropic Python SDK. Load ANTHROPIC_API_KEY from env.
```

-----

### SESSION 8 — News Arbitrage Strategy

**Prompt:**

```
Build bot/strategies/news_arbitrage.py.

This strategy enters markets quickly after relevant news breaks, before prices fully adjust.

Create a NewsArbitrageStrategy class:

- async generate_signals(classified_headline: ClassifiedHeadline, client: KalshiClient) -> list[TradeSignal]
  
  Logic:
  1. If classified_headline.relevant is False or confidence < 0.6, return []
  2. Fetch open markets matching affected_categories
  3. For each market, check if the headline is relevant to this specific market (simple keyword matching between headline and market title)
  4. Fetch current orderbook for matching markets
  5. Check if price has already moved — if the relevant side has moved more than 0.05 in the last 5 minutes, skip (too late)
  6. Generate a TradeSignal:
     - side: 'yes' if direction == 'yes_up' else 'no'
     - entry_price: current best ask for that side
     - our_probability: market_price + 0.08  (assume 8% mispricing window on news)
     - expected_value: 0.08 * confidence
     - time_to_resolution: hours until market closes
     - confidence: classified_headline.confidence * 0.8  (discount for news uncertainty)
     - news_headline: classified_headline.headline
     - reasoning: f"News: '{headline}' — expect {side} to move up. {reasoning}"
     - strategy: 'news_arbitrage'
  
  7. Only return signals where time_to_resolution > 2 hours (enough time for the trade to matter)

- keyword_match(headline: str, market_title: str) -> bool
  Simple check: do 2+ significant words (>4 chars) from the headline appear in the market title?
  Case-insensitive. Return True if match found.
```

-----

### SESSION 9 — Main Scanner + Executor

**Prompt:**

```
Build bot/core/scanner.py and bot/core/executor.py.

--- scanner.py ---
Create a Scanner class that orchestrates all strategies:

- async run_scan(client: KalshiClient, db_session, bankroll: float) -> list[TradeSignal]
  1. Check if bot is enabled (read from settings table). If not, return [].
  2. Check daily loss limit via RiskManager. If hit, log warning and return [].
  3. Run BondStrategy.scan() if bond_strategy_enabled setting is true
  4. Run MarketMakingStrategy.scan() if market_making_enabled setting is true
  5. Aggregate all signals, deduplicate by ticker+side
  6. Run each through RiskManager.check_trade() — filter to approved only, use recommended_size
  7. Run through SignalScorer.filter_minimum_edge() and SignalScorer.rank_signals()
  8. Return top 5 signals maximum per scan cycle (avoid overtrading)

--- executor.py ---
Create an Executor class:

- async execute_signal(signal: TradeSignal, client: KalshiClient, db_session) -> bool
  1. Place order via client.place_order()
  2. If order placed successfully, insert into trades table (status='open') and positions table
  3. Log the trade with all details
  4. Return True on success, False on failure

- async monitor_positions(client: KalshiClient, db_session) -> None
  1. Fetch all open positions from DB
  2. For each position, fetch current market price
  3. Update unrealized_pnl in positions table
  4. Check exit conditions:
     a. Market is resolved → close the position, update trades table, trigger reflection
     b. Position has lost > 50% of entry value (stop loss) → place market sell order
     c. For bond strategy: if price has moved against us > 0.10 → alert (log ERROR)
  5. For closed positions, calculate net_pnl (accounting for fees: $0.07 per contract per side)

- async close_position(position: dict, client: KalshiClient, db_session, reason: str) -> None
  Cancel any open orders for this market, record final PnL in trades table, remove from positions table.
```

-----

### SESSION 10 — Reflection Engine

**Prompt:**

```
Build bot/intelligence/reflection_engine.py.

Create a ReflectionEngine class that uses Claude to learn from completed trades.

- async reflect_on_trade(trade: dict, db_session) -> None
  Called after a trade is closed/resolved.
  
  Build a prompt for Claude (claude-haiku-4-5):
  System: "You are a trading journal AI for a prediction market bot. Analyze trades honestly and provide actionable insights. Return JSON only."
  
  User: "Analyze this completed trade:\n
  Market: {market_title}\n
  Strategy: {strategy}\n
  Side: {side}\n
  Entry Price: {entry_price}\n
  Exit Price: {exit_price}\n
  Net PnL: ${net_pnl}\n
  Result: {'WIN' if net_pnl > 0 else 'LOSS'}\n
  Original Reasoning: {entry_reasoning}\n
  Time Held: {hours} hours\n
  
  Return JSON: {\"summary\": \"2 sentence summary\", \"what_worked\": \"what went right or null\", \"what_failed\": \"what went wrong or null\", \"confidence_score\": 1-10, \"strategy_suggestion\": \"one actionable improvement for next time\"}"
  
  Parse response, insert into reflections table linked to trade_id.

- async generate_weekly_report(db_session) -> None
  Called every Monday at 00:00.
  Fetch all reflections and trades from the past 7 days.
  Build summary stats: total trades, win rate, net PnL, best strategy, worst strategy.
  Call Claude to synthesize into a weekly_reflections record with key_learnings.
  Insert into weekly_reflections table.

- async get_recent_learnings(db_session, limit: int = 5) -> str
  Fetch the 5 most recent reflection summaries.
  Return as formatted string to inject into strategy prompts for continuous learning.
```

-----

### SESSION 11 — FastAPI Backend

**Prompt:**

```
Build the complete FastAPI backend in api/.

--- api/database.py ---
Async SQLAlchemy setup. Create async engine from DATABASE_URL env var. Create async session factory. Create get_db() dependency.

--- api/models.py ---
SQLAlchemy ORM models matching the schema.sql tables: Trade, Position, Reflection, WeeklyReflection, Setting.
Use mapped_column with proper types.

--- api/main.py ---
FastAPI app with:
- CORS enabled for all origins (so the PWA can call it)
- Bearer token auth middleware: check Authorization header against API_BEARER_TOKEN env var. Return 401 if missing/wrong.
- Include all routers
- Startup event: run schema.sql to init tables if not exist
- Health check endpoint: GET /health → {"status": "ok", "bot_enabled": bool}

--- api/routes/dashboard.py ---
GET /dashboard
Returns:
{
  "bankroll": float,
  "total_pnl": float,
  "today_pnl": float,
  "win_rate": float,
  "total_trades": int,
  "open_positions": int,
  "unrealized_pnl": float,
  "best_strategy": str,
  "streak": int  (current win/loss streak)
}

--- api/routes/trades.py ---
GET /trades?page=1&limit=20&strategy=all&status=all
Returns paginated trade history with all fields.

GET /trades/{id}
Returns single trade with its reflection if exists.

--- api/routes/positions.py ---
GET /positions
Returns all open positions with unrealized PnL, market title, strategy, side, size, entry_price, current_price.

--- api/routes/reflections.py ---
GET /reflections?page=1&limit=20
Returns paginated AI reflection logs newest first.

GET /reflections/weekly
Returns all weekly summary reports.

--- api/routes/controls.py ---
POST /controls/pause  → sets bot_enabled=false in settings
POST /controls/resume → sets bot_enabled=true in settings
POST /controls/settings (body: {max_position_pct: float, daily_loss_limit_pct: float}) → update settings
GET /controls/settings → return current settings

All routes return proper HTTP status codes and error messages.
```

-----

### SESSION 12 — Bot Main Entry Point

**Prompt:**

```
Build bot/main.py — the main entry point that wires everything together and runs the bot.

Use APScheduler with AsyncIOScheduler.

Jobs to schedule:
1. scan_and_trade() — every 60 seconds
   - Run Scanner.run_scan()
   - For each approved signal, run Executor.execute_signal()
   - Log summary: X signals found, Y executed

2. monitor_positions() — every 30 seconds
   - Run Executor.monitor_positions()

3. daily_reflection() — every day at 00:05 UTC
   - Run ReflectionEngine.generate_weekly_report() (it will check if it's Monday internally)

4. news_polling() — continuous, runs in background
   - Start NewsListener.start_polling() with callback that:
     a. Classifies the headline
     b. If relevant, runs NewsArbitrageStrategy.generate_signals()
     c. If signals returned, runs them through RiskManager and Executor immediately (news is time-sensitive)

Startup sequence:
1. Load .env
2. Initialize database (run schema.sql)
3. Initialize KalshiClient
4. Initialize all strategy and intelligence classes
5. Create db session factory
6. Start APScheduler
7. Log "Kalshi Bot started. Strategies active: [list]"
8. Keep running (asyncio event loop)

On shutdown (SIGTERM/SIGINT):
- Cancel all open limit orders placed by market making strategy
- Log "Kalshi Bot shutting down cleanly"

Include a BOT_ENABLED check at the top of scan_and_trade() — if false, skip the cycle and log "Bot paused."
```

-----

### SESSION 13 — Next.js PWA UI

**Prompt:**

```
Build a Next.js 14 mobile-first PWA in the ui/ directory. This will be installed on an iPhone home screen.

Setup:
- Use Next.js App Router
- Tailwind CSS with dark mode (dark background: #0a0a0a, cards: #1a1a1a)
- Install: next-pwa for service worker, recharts for charts
- API base URL from NEXT_PUBLIC_API_URL env variable
- All API calls include Authorization: Bearer {NEXT_PUBLIC_API_TOKEN} header
- lib/api.ts: typed fetch wrapper for all backend endpoints

PWA Config (public/manifest.json):
{
  "name": "Kalshi Bot",
  "short_name": "KalshiBot",
  "theme_color": "#0a0a0a",
  "background_color": "#0a0a0a",
  "display": "standalone",
  "orientation": "portrait",
  "start_url": "/",
  "icons": [{ "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" }]
}

Pages to build:

1. app/page.tsx — Dashboard
- Top bar: "Kalshi Bot" title + green/red dot (bot status)
- Bankroll card: large number showing current bankroll, +/- change today in green/red
- Stats row: Win Rate % | Total PnL | Open Positions (3 stat cards)
- Mini chart: 7-day PnL line chart using recharts
- Open Positions section: list of PositionCard components
- Bottom nav: Dashboard | Trades | Reflections | Controls

2. app/trades/page.tsx — Trade Feed
- Filter chips: All | Bond | Market Making | News | Open | Closed
- Scrollable list of TradeCard components
- TradeCard shows: market title (truncated), strategy badge (colored), side badge, PnL chip (green/red), time ago
- Tap to expand: shows full details including entry reasoning and reflection summary if available

3. app/reflections/page.tsx — AI Logs
- Tab toggle: Trade Reflections | Weekly Reports
- Card feed showing AI-generated reflections
- Each card: trade title, confidence score (1-10 with color), summary text, what_worked/what_failed in small text
- Weekly reports show as larger summary cards with key_learnings

4. app/controls/page.tsx — Bot Controls
- Large toggle: BOT ACTIVE / PAUSED (sends POST /controls/pause or resume)
- Strategy toggles: Bond | Market Making | News Arbitrage (individual on/off)  
- Risk Settings section:
  - Max Position Size slider (5%–25%)
  - Daily Loss Limit slider (1%–10%)
  - Save button
- Danger zone: shows today's loss vs limit with progress bar
- Last updated timestamp

Design requirements:
- Dark mode only
- Feels like a real trading terminal on mobile
- Green (#00d4aa) for positive/active, Red (#ff4444) for negative/paused
- Orange (#ff8c00) for neutral/warning
- Smooth transitions between pages
- Loading skeletons while data fetches
- Pull-to-refresh on dashboard
- Auto-refresh dashboard every 30 seconds
```

-----

### SESSION 14 — Deployment

**Prompt:**

```
Set up deployment configuration for Railway (bot + API) and Vercel (UI).

--- Dockerfile ---
Multi-stage Python 3.11 Dockerfile.
Stage 1: install dependencies from requirements.txt
Stage 2: copy bot/ and api/ directories, set working directory
CMD: run both bot/main.py and api/main.py — use a shell script entrypoint that starts the FastAPI server (uvicorn api.main:app --host 0.0.0.0 --port $PORT) and the bot (python -m bot.main) simultaneously using & and wait.

--- railway.toml ---
[build]
builder = "dockerfile"

[deploy]
startCommand = "./entrypoint.sh"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "on_failure"

--- vercel.json (in ui/) ---
Standard Next.js vercel config. Set framework to nextjs.

--- entrypoint.sh ---
#!/bin/bash
# Start FastAPI
uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} &
# Start bot
python -m bot.main &
# Wait for either to exit
wait -n
exit $?

--- README.md (complete version) ---
Write a complete README with:
1. Project overview and strategy descriptions
2. Prerequisites list
3. Local development setup (clone → pip install → set .env → run schema.sql → uvicorn + python main.py)
4. Supabase setup (create project, run schema.sql in SQL editor, copy DATABASE_URL)
5. Railway deployment steps
6. Vercel deployment steps  
7. iPhone PWA installation instructions (Safari → Share → Add to Home Screen)
8. First-time configuration (how to set initial bankroll in settings table)
9. Monitoring tips
10. Risk warnings and disclaimer
```

-----

## Key Architecture Decisions

- **No external task queues** (no Celery/Redis) — APScheduler keeps it simple for solo deployment
- **Single Railway service** runs both the bot and API to minimize cost
- **Supabase** provides hosted PostgreSQL with a nice UI for viewing trades directly
- **Half-Kelly sizing** used throughout — never full Kelly, reduces variance significantly
- **Bond strategy first** — safest, most automatable. Activate MM and news arbitrage after 2-3 weeks of stable operation
- **Reflection engine is non-blocking** — trade closures don’t wait for Claude API response

## Risk Parameters (Starting Defaults)

|Parameter              |Default        |Rationale                          |
|-----------------------|---------------|-----------------------------------|
|Max position size      |15% of bankroll|Max $750 on a $5K account          |
|Daily loss limit       |3% of bankroll |Stop at -$150/day                  |
|Max total exposure     |60%            |Keep $2K always liquid             |
|Bond strategy min price|94¢            |Minimum certainty threshold        |
|Min market volume      |$5,000         |Ensure exit liquidity              |
|News entry window      |5 minutes      |After that, market has priced it in|

## Accounts / Services Needed

|Service  |URL                  |Purpose                 |Cost                     |
|---------|---------------------|------------------------|-------------------------|
|Kalshi   |kalshi.com           |Trading platform        |Free (trading fees apply)|
|Supabase |supabase.com         |PostgreSQL database     |Free tier                |
|Railway  |railway.app          |Bot + API hosting       |~$5/mo after free tier   |
|Vercel   |vercel.com           |UI hosting              |Free tier                |
|Anthropic|console.anthropic.com|Claude API (reflections)|~$1-3/mo at this scale   |
|GNews    |gnews.io             |News API                |Free tier (100 req/day)  |

# Kalshi Bot — Step-by-Step Setup

Use this checklist to get the bot running locally or deployed.

---

## 1. Prerequisites

- **Python 3.11+** — `python3 --version`
- **Node.js 18+** — for the UI (optional for bot-only)
- **Accounts** (sign up before continuing):
  - [Kalshi](https://kalshi.com) — enable API access in account settings
  - [Supabase](https://supabase.com) — free tier
  - [Anthropic](https://console.anthropic.com) — API key for reflection engine
  - [GNews](https://gnews.io) — free tier for news monitoring

---

## 2. Environment variables

```bash
cp .env.example .env
```

Edit `.env` and set **at minimum**:

| Variable | Where to get it |
|----------|-----------------|
| `KALSHI_API_KEY` | Kalshi → Account → API |
| `KALSHI_API_SECRET` | Same as above |
| `DATABASE_URL` | See step 3 (Supabase) |
| `MINIMAX_CODING_PLAN_API_KEY` **or** `ANTHROPIC_API_KEY` | MiniMax: platform.minimaxi.com (Coding Plan key) — or Anthropic console |
| `GNEWS_API_KEY` | gnews.io (dashboard) |
| `API_BEARER_TOKEN` | Pick a long random string (e.g. `openssl rand -hex 32`) |

Leave `KALSHI_BASE_URL` as-is (default: api.elections.kalshi.com). For **paper trading**, use Kalshi’s demo URL if they provide one (check their docs).

Optional: adjust `INITIAL_BANKROLL`, `BOT_ENABLED` (start with `false` until you’re ready), and strategy params.

---

## 3. Database (Supabase)

1. Create a new project at [supabase.com](https://supabase.com).
2. In the project: **SQL Editor** → New query.
3. Paste the full contents of `db/schema.sql` and run it.
4. **Project Settings** → **Database** → copy the **Connection string** (URI).
5. Change the URI for async Python:
   - Replace `postgres://` with `postgresql+asyncpg://`
   - If it has `postgresql://`, still use `postgresql+asyncpg://`
   - Example:  
     `postgresql+asyncpg://postgres.[project-ref]:[YOUR-PASSWORD]@aws-0-[region].pooler.supabase.com:6543/postgres`
6. Put that value in `.env` as `DATABASE_URL`.

---

## 4. Run locally

Use a **virtual environment** (recommended on macOS):

```bash
cd /Users/fredm/Auto-Trading-Bot
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Terminal 1 — API (required for the bot’s DB access)

```bash
cd /Users/fredm/Auto-Trading-Bot
source .venv/bin/activate
uvicorn api.main:app --reload --port 8001
```

### Terminal 2 — Bot

```bash
cd /Users/fredm/Auto-Trading-Bot
source .venv/bin/activate
python -m bot.main
```

The bot will scan every 60 seconds. Keep `BOT_ENABLED=false` in `.env` until you’ve verified the API and DB connection; then set it to `true` or turn it on via the dashboard.

### Terminal 3 — UI (optional)

```bash
cd /Users/fredm/Auto-Trading-Bot/ui
npm install
npm run dev
```

Set in `ui/.env.local` (create if needed):

- `NEXT_PUBLIC_API_URL=http://localhost:8001`
- `NEXT_PUBLIC_API_TOKEN=<same as API_BEARER_TOKEN in main .env>`

Open http://localhost:3000 for the dashboard.

---

## 5. First-time checks

- **API health:** Open http://localhost:8001/health — should return OK.
- **Bot logs:** In the bot terminal you should see scan cycles (and “bot is paused” if `BOT_ENABLED=false`).
- **Database:** In Supabase **Table Editor**, confirm tables `settings`, `trades`, `positions` exist and `settings` has rows (e.g. `current_bankroll`).

Set initial bankroll in DB or via the Controls page:

```sql
UPDATE settings SET value = '5000' WHERE key = 'current_bankroll';
```

---

## 6. Enable trading

1. Confirm Kalshi API keys are for the environment you want (live vs demo).
2. Set `BOT_ENABLED=true` in `.env` or in the DB:  
   `UPDATE settings SET value = 'true' WHERE key = 'bot_enabled';`
3. Start with a small `INITIAL_BANKROLL` / `current_bankroll` and watch the first few cycles.

---

## 7. Deploy (optional)

- **Bot + API:** Use the project’s `Dockerfile` on [Railway](https://railway.app); add all `.env` variables in Railway’s dashboard.
- **UI:** Deploy the `ui/` folder to [Vercel](https://vercel.com); set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_API_TOKEN` in Vercel env.

See the main [README.md](README.md) for deployment details.

---

## Troubleshooting

| Issue | What to check |
|-------|-------------------------------|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` from repo root. |
| DB connection errors | `DATABASE_URL` must use `postgresql+asyncpg://` and correct password; check Supabase pooler port (often 6543). |
| 401 from API | `Authorization: Bearer <API_BEARER_TOKEN>`; token must match `.env` and UI `NEXT_PUBLIC_API_TOKEN`. |
| Bot does nothing | `bot_enabled` in `settings` and `BOT_ENABLED` in env; check logs for “paused” or risk limit messages. |
| Kalshi API errors | Confirm API key/secret and that the account has API access enabled. |

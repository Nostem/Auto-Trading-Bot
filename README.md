# Kalshi Prediction Market Trading Bot

An autonomous trading bot for [Kalshi](https://kalshi.com), a CFTC-regulated US prediction market. It scans for profitable opportunities, executes trades, manages risk, reflects on outcomes using AI, and exposes a mobile-first PWA dashboard for iPhone monitoring and control.

## Strategies

1. **Bond Strategy** — Trade markets priced at 94¢+ with resolution within 72 hours. Earn 2–6% per trade with low risk.
2. **Market Making** — Place limit orders on both YES and NO sides of liquid markets, earning the bid-ask spread.
3. **News-Driven Arbitrage** — Monitor RSS/news feeds, enter markets within 30–300 seconds of a relevant headline before prices adjust.

## Prerequisites

- Python 3.11+
- Node.js 18+
- [Supabase](https://supabase.com) account (free tier)
- [Kalshi](https://kalshi.com) account with API access
- [Anthropic API key](https://console.anthropic.com) (for AI reflection engine)
- [GNews API key](https://gnews.io) (for news monitoring, free tier)
- [Railway](https://railway.app) account (for bot + API hosting)
- [Vercel](https://vercel.com) account (for UI hosting)

## Local Development Setup

```bash
# Clone the repo
git clone <repo-url>
cd kalshi-bot

# Install Python dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Initialize the database (run schema.sql in Supabase SQL editor, or locally):
psql $DATABASE_URL < db/schema.sql

# Start the API server
uvicorn api.main:app --reload --port 8000

# In a separate terminal, start the bot
python -m bot.main
```

## Supabase Setup

1. Go to [supabase.com](https://supabase.com) and create a new project.
2. In the SQL editor, paste the contents of `db/schema.sql` and run it.
3. In Project Settings → Database, copy the connection string (URI format).
4. Replace `postgres://` with `postgresql+asyncpg://` and put it in your `.env` as `DATABASE_URL`.

## Railway Deployment

1. Connect your GitHub repo to Railway.
2. Set all environment variables from `.env.example` in Railway's Variables tab.
3. Railway will detect the `Dockerfile` and build automatically.
4. The service exposes the API on the `PORT` environment variable Railway provides.

## Vercel Deployment

1. In the `ui/` directory, run `npm install` then `npm run build` to verify the build.
2. Connect the repo to Vercel, setting the root directory to `ui/`.
3. Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_API_TOKEN` in Vercel environment variables.
4. Deploy — Vercel handles the rest automatically.

## iPhone PWA Installation

1. Open the deployed Vercel URL in Safari on your iPhone.
2. Tap the Share button (box with arrow).
3. Scroll down and tap **Add to Home Screen**.
4. Name it "Kalshi Bot" and tap Add.

The app will launch in standalone mode (no browser chrome) from your home screen.

## First-Time Configuration

After deploying, set your initial bankroll in the `settings` table:

```sql
UPDATE settings SET value = '5000' WHERE key = 'current_bankroll';
```

Or use the Controls page in the PWA to adjust risk parameters.

## Monitoring Tips

- Check the dashboard daily for the first two weeks.
- Watch the daily loss limit — the bot pauses automatically if hit.
- Review AI reflections weekly for strategy improvement suggestions.
- Monitor open positions, especially bond trades near resolution.
- Set up Railway log alerts for `ERROR` and `CRITICAL` level messages.

## Risk Warnings

- This bot trades real money on real markets. Past performance does not guarantee future results.
- Prediction markets carry inherent risk. The bond strategy can lose on unexpected events.
- **Always start with paper trading or small amounts** while validating bot behavior.
- Monitor the bot daily for the first two weeks before trusting full autonomy.
- Never put money into this bot that you cannot afford to lose entirely.
- This software is provided as-is with no warranty. You are responsible for all trading decisions.

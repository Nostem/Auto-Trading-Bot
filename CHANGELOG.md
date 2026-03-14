# Kalshi Bot Change Log

## How to Deploy Strategy Changes

Every time we adjust strategies, follow this workflow:

1. **Make code changes** locally (or via opencode)
2. **Commit & push** to trigger Railway auto-deploy
3. **Redeploy worker** to pick up changes immediately:
   ```bash
   cd ~/auto-trading-bot
   railway redeploy -s "Auto-Trading-Worker" -y
   railway redeploy -s "Auto-Trading-Bot" -y
   ```
4. **Archive & reset** — wipes all positions, trades, PnL, and resets bankroll:
   ```bash
   python -m scripts.archive_and_start_fresh \
     --label paper-v4 \
     --strategy-version v4 \
     --bankroll 1000 \
     --enable-bot
   ```
5. **Verify** in the UI — should show $1000 bankroll, 0 trades, 0 positions

---

## 2026-03-14 — v8: Multi-Model Ensemble, NWS Station Coords, Higher Min Edge

- CRITICAL: Added ECMWF IFS ensemble (51 members) alongside GFS (31 members) = 82 total ensemble members. X/Twitter research shows profitable bots use 4-5 forecast sources; single-source GFS had systematic cold bias causing consistent underestimation of high temps.
- Fixed coordinates to match NWS settlement stations: NYC→Central Park (KNYC), Chicago→Midway (KMDW), Miami→MIA airport. Kalshi settles on NWS Daily Climate Report from specific stations, not city center coordinates.
- Minimum edge raised: 8% → 12%. X research shows successful traders use 15%+ edge thresholds.
- Payoff-scaled edge now starts from 12% base (was 8%).
- v7.1 post-mortem: 2W/7L (-$123.41). GFS underestimated high temps by 3-5°F on 5/7 losses. Multi-model ensemble should reduce this systematic bias.

## 2026-03-11 — v7.1: Unblock Weather Signals, Disable BTC, Payoff-Scaled Edge

- CRITICAL: v7 filters were too tight — zero weather signals generated in 2 days. Loosened filters while adding smarter edge requirements.
- Near-threshold guard: 5°F → 3°F — daily max/min fix (v6) makes model accurate enough at 3°F gap.
- Entry cap: 55¢ → 75¢ — but with payoff-scaled minimum edge (8% at 50¢, ~15% at 65¢, ~20% at 75¢).
- Removed divergence check — was blocking high-confidence ensemble signals. When 31/31 members agree, that IS the edge.
- BTC strategy DISABLED — 4W/7L, -$23.17, all wins were $0.33, RSI signals had 0.00-0.02 strength. No real edge.

## 2026-03-09 — v7: Value Zone, Wider Threshold Guard, Tighter Divergence

- Weather max entry: 82¢ → 55¢ — forces trades into "value zone" where risk ≈ reward. At 70¢ entry you need 77% win rate; at 40¢ you need 45%.
- Near-threshold guard: 2°F → 5°F — ensemble model unreliable within 5°F of threshold (v6 showed 3-5°F misses causing most losses).
- Market divergence cutoff: >50pp → ≥40pp — trust the market more when it strongly disagrees with ensemble.
- Force redeploy to ensure weather stop-loss disable is active on Railway.

## 2026-03-07 — v6: Fix Temp Window, No Weather Stop-Loss, Market Sanity Check

- CRITICAL FIX: Weather now uses daily MAX temp (for HIGH markets) and daily MIN (for LOW markets) across all forecast hours, instead of the single temperature at market close time. Previous versions compared evening temps to daytime high thresholds — guaranteed wrong.
- Disabled stop-loss for weather — hold to resolution only. Stop-loss was killing correct positions within minutes before they could resolve.
- Added market sanity check: skip trades where model diverges from market by >50 percentage points. When market strongly disagrees with ensemble, something is off.
- Reduced position sizing from 5% to 3% of bankroll ($30 max per trade) since weather trades now hold to resolution with no stop-loss safety net.

## 2026-03-06 — v5: Hold-to-Resolution, Entry Caps, Vol-Based BTC

- Weather: DISABLED pre-expiry exit — hold to resolution (7 wins were turned into losses by force-closing 5min early)
- Weather: Max entry price capped at 82¢ for YES and NO — poor risk/reward above this
- Weather: Min YES entry lowered to 25¢ (was 70¢) for better value plays
- Resolution PnL fix: settled trades now use binary payout ($1/$0) with single-side fee instead of market price + double fee
- BTC: Replaced RSI heuristic edge with log-normal volatility probability model using realized vol from 15m candles
- BTC: RSI thresholds widened 35/65 → 40/60 for more signal frequency
- BTC: Added 2% minimum edge (fee buffer) — won't trade unless model prob exceeds market price by 2%+

## 2026-03-04 — v4 Strategy Overhaul: Ensemble Weather, Multi-Indicator BTC, Position Sizing Fix

- Fixed critical bug: `RiskManager` now reads `max_position_pct` from DB settings (was ignoring UI setting, using 15% instead of user-set 5%).
- Weather: switched from NOAA point forecast to 31-member GFS ensemble via Open-Meteo (free API).
- Weather: raised min edge 4% -> 8%, added ensemble agreement filter (>80%), near-threshold guard (2F).
- Kelly sizing: reduced from half-Kelly (50%) to 15% fraction to reduce blowup risk from probability errors.
- BTC: added VWAP deviation, multi-timeframe momentum, SMA crossover + convergence filter (2+ must agree).
- Max position default: 15% -> 5% of bankroll.

## 2026-03-04 — Strategy Overhaul (Session: Paper Trading v2)
Changes made by: Koempassu's Klaw (via opencode)

### 1. Disable Market Making Strategy
- Set market_making_enabled default to 'false' in scanner.py
- Reason: Not real market-making. Takes one-sided directional bets with fake edge (our_probability = entry_price + 0.01). Every single trade lost money. 38 trades today, 0 wins, -$77.85.

### 2. Enable Weather Strategy
- Set weather_strategy_enabled default to 'true' in scanner.py
- Reason: Only strategy with a real data-driven edge model (NOAA forecasts + normal distribution). Well-calibrated forecasts give genuine probability estimates.

### 3. Tighten Bond Strategy
- In bond_strategy.py, change BOND_MIN_PRICE default from 0.88 to 0.94
- Reason: At 88c, max profit after fees is ~$0.20/trade. At 94c, only truly near-certain outcomes qualify, and the higher price means less capital at risk.

### 4. Loosen BTC RSI Triggers
- In btc_strategy.py, change _RSI_OVERBOUGHT from 70 to 65
- In btc_strategy.py, change _RSI_OVERSOLD from 30 to 35
- Reason: 30/70 is too tight — strategy never triggers. 35/65 will generate more signals while still requiring meaningful momentum.

### 5. Wire Up Kelly Sizing
- In scanner.py or executor.py, when a signal is approved, call risk_manager.calculate_kelly_size() to compute position size instead of using fixed proposed_size from strategies.
- Pass signal.our_probability, signal.entry_price, and current bankroll.
- Use the Kelly-recommended size as proposed_size (it already uses half-Kelly for safety).
- Keep the existing max position size clamp in risk_manager.check_trade() as a ceiling.

### 6. Fix SQLAlchemy Stale Session Bug
- In executor.py monitor_positions(), the error is: 'UPDATE statement on table positions expected to update 1 row(s); 0 were matched'
- This happens when a position is deleted by one iteration but another iteration tries to update it.
- Fix: wrap each position check in its own try/except with session.rollback(), and re-query the position before updating to confirm it still exists.

### 7. Reset Bankroll
- In the start_new_run.py script or directly: update the settings table to set current_bankroll back to 1000.00
- Create a new script scripts/reset_bankroll.py that sets current_bankroll=1000.00 in the settings table and logs the reset.

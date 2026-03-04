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

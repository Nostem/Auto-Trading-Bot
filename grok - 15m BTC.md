### Disclaimer
Trading involves significant risk of loss and is not suitable for everyone. No strategy guarantees consistent returns, as markets are volatile and unpredictable. Past performance does not indicate future results. This is for educational purposes only—test in demo mode first, and consult a financial advisor. Kalshi is a regulated prediction market, but BTC contracts are binary events (e.g., "Will BTC close above $X today?"), not direct BTC trading. This strategy uses 15-minute BTC price data to inform trades on Kalshi's longer-term BTC event contracts, such as daily thresholds. Actual returns depend on market conditions, execution, fees (Kalshi charges ~0.5-1% per trade), and your risk management.

### Strategy Overview
This bot implements a simple momentum-based strategy on 15-minute (15m) Bitcoin (BTC) candles to trade Kalshi's BTC-related event contracts. 

- **Core Idea**: Monitor BTC/USDT price every 15 minutes using external data (e.g., from Binance via CCXT library). Calculate a 14-period RSI (Relative Strength Index) on 15m candles. If RSI > 70 (overbought), sell or buy NO on a bullish Kalshi contract (betting against upside). If RSI < 30 (oversold), buy YES on a bullish contract (betting on rebound). Focus on daily BTC contracts like "Will BTC max > $X today?" or similar thresholds from Kalshi's API.
- **Assumptions**: 
  - Targets open BTC markets (e.g., series like KXBTCMAXY or daily highs). Bot filters for active BTC events.
  - Position sizing: Fixed $10 per trade (10 contracts at ~$0.50 implied).
  - Runs in a loop, checking every 15 minutes during trading hours (Kalshi: 8 AM - 8 PM ET, but BTC 24/7).
  - Edge: Short-term oversold/overbought signals to predict daily outcomes. Backtesting (not included) could show ~55-60% win rate in trending markets, but expect drawdowns.
- **Risks**: Overfitting to historical data, slippage in low-liquidity markets, API rate limits, and BTC volatility. Implement stops (e.g., exit if position > max loss).
- **Requirements**: Python 3+, pip install ccxt requests ta-lib (for indicators; or implement manually). Set environment variables for KALSHI_EMAIL, KALSHI_PASSWORD. Fund Kalshi account. Use demo API for testing (https://demo.kalshi.com/trade-api/v2).

### Full Python Bot Code
Below is the complete, runnable script. Save as `kalshi_btc_bot.py` and run with `python kalshi_btc_bot.py`. It logs actions to console/file.

```python
import os
import time
import requests
import ccxt  # For BTC data; pip install ccxt
import talib  # For RSI; pip install ta-lib (or implement manually)
import numpy as np
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(filename='kalshi_btc_bot.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Kalshi API config (use demo for testing)
KALSHI_API_URL = 'https://trading-api.kalshi.com/trade-api/v2'  # Demo: https://demo.kalshi.com/trade-api/v2
EMAIL = os.getenv('KALSHI_EMAIL')
PASSWORD = os.getenv('KALSHI_PASSWORD')

# Strategy params
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
TRADE_AMOUNT = 10  # Contracts (e.g., $10 at $0.50 price)
MAX_POSITION = 50  # Max contracts held
POLL_INTERVAL = 900  # 15 minutes in seconds
TARGET_SERIES = ['KXBTCMAX', 'KXBTCMIN', 'KXBTCMAXY']  # BTC max/min series from Kalshi

# Authenticate with Kalshi
def kalshi_login():
    url = f'{KALSHI_API_URL}/login'
    payload = {'email': EMAIL, 'password': PASSWORD}
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        return response.json()['token']
    else:
        logging.error(f'Login failed: {response.text}')
        raise Exception('Kalshi login failed')

# Get headers with token
token = kalshi_login()
headers = {'Authorization': f'Bearer {token}'}

# Fetch active BTC markets from Kalshi
def get_btc_markets():
    markets = []
    for series in TARGET_SERIES:
        url = f'{KALSHI_API_URL}/series/{series}/events?status=open'
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            events = response.json().get('events', [])
            for event in events:
                event_url = f'{KALSHI_API_URL}/events/{event["event_ticker"]}'
                event_resp = requests.get(event_url, headers=headers)
                if event_resp.status_code == 200:
                    markets.extend(event_resp.json().get('markets', []))
    return markets  # List of dicts with 'ticker', 'yes_bid', 'yes_ask', etc.

# Fetch 15m BTC OHLCV data (last 100 candles)
def get_btc_15m_data():
    exchange = ccxt.binance()  # Or coingecko if preferred
    ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='15m', limit=100)
    closes = np.array([c[4] for c in ohlcv])  # Close prices
    return closes

# Calculate RSI
def calculate_rsi(closes):
    return talib.RSI(closes, timeperiod=RSI_PERIOD)[-1]  # Latest RSI

# Place order on Kalshi
def place_order(ticker, side, action, count, price=None):
    url = f'{KALSHI_API_URL}/orders'
    payload = {
        'ticker': ticker,
        'side': side,  # 'yes' or 'no'
        'action': action,  # 'buy' or 'sell'
        'type': 'market' if price is None else 'limit',
        'count': count
    }
    if price:
        payload['price'] = price
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        logging.info(f'Order placed: {payload} - {response.json()}')
        return response.json()
    else:
        logging.error(f'Order failed: {response.text}')
        return None

# Get current positions (simplified; fetch portfolio for full)
def get_positions():
    url = f'{KALSHI_API_URL}/portfolio/positions'
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('positions', [])
    return []

# Main strategy logic
def execute_strategy():
    # Get BTC data and RSI
    closes = get_btc_15m_data()
    rsi = calculate_rsi(closes)
    current_price = closes[-1]
    logging.info(f'Current BTC: ${current_price:.2f}, RSI: {rsi:.2f}')
    
    # Get open BTC markets
    markets = get_btc_markets()
    if not markets:
        logging.info('No open BTC markets found.')
        return
    
    # Filter for a target market (e.g., highest volume daily max > current-ish threshold)
    target_market = max(markets, key=lambda m: m.get('volume', 0))  # Simplest: highest volume
    ticker = target_market['ticker']
    yes_bid = target_market['yes_bid'] / 100  # Convert cents to prob
    yes_ask = target_market['yes_ask'] / 100
    
    # Check positions
    positions = get_positions()
    current_pos = next((p['qty_owned'] for p in positions if p['ticker'] == ticker), 0)
    
    # Decision: Assume bullish contract (e.g., higher max)
    if rsi < RSI_OVERSOLD and current_pos < MAX_POSITION:
        # Oversold: Buy YES (expect rebound)
        place_order(ticker, 'yes', 'buy', TRADE_AMOUNT)
    elif rsi > RSI_OVERBOUGHT and current_pos > 0:
        # Overbought: Sell YES or buy NO
        place_order(ticker, 'yes', 'sell', min(TRADE_AMOUNT, current_pos))
    else:
        logging.info('No signal.')

# Run bot in loop
if __name__ == '__main__':
    while True:
        try:
            now = datetime.now()
            if 8 <= now.hour < 20:  # Kalshi trading hours (ET)
                execute_strategy()
            else:
                logging.info('Outside trading hours.')
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            logging.error(f'Error: {e}')
            time.sleep(60)  # Retry after 1 min
```

### Detailed Explanation
1. **Authentication**: Logs in to Kalshi API to get a Bearer token. Refresh if expired (add logic if needed).
2. **Data Fetching**:
   - BTC 15m data: Uses CCXT to pull from Binance (free, no API key needed for public data). Gets last 100 closes for RSI.
   - Kalshi Markets: Fetches open events in BTC series, then markets. Targets highest volume for simplicity—customize to specific tickers (e.g., parse threshold from ticker like 'KXBTCMAXY-26DEC31-99999.99').
3. **Indicator Calculation**: RSI via TA-Lib. If not installed, implement manually:
      ```python
      def calculate_rsi(closes):
          deltas = np.diff(closes)
          up = deltas.clip(min=0).mean()
          down = -deltas.clip(max=0).mean()
          return 100 - (100 / (1 + (up / down))) if down else 100
      ```
   - Use last RSI value.
4. **Trading Logic**:
   - If oversold (<30), buy YES on bullish contract (e.g., higher max).
   - If overbought (>70), sell existing YES.
   - Caps positions to avoid overexposure.
   - Uses market orders for speed; switch to limit for better prices.
5. **Loop & Scheduling**: Runs every 15m. Checks Kalshi hours to avoid invalid trades.
6. **Enhancements for Consistency**:
   - **Risk Management**: Add trailing stops—monitor via `/portfolio/settlements` and sell if loss >10%.
   - **Backtesting**: Use historical BTC data from CCXT to simulate (e.g., loop over past 15m intervals).
   - **Multiple Markets**: Trade across several BTC thresholds for diversification.
   - **Alerts**: Integrate Telegram/Slack for notifications.
   - **Edge Testing**: In bull markets, this could yield 1-2% daily on capital, but expect 40-50% drawdowns. Optimize RSI thresholds via optimization libs like scipy.
   - **Costs**: Kalshi fees ~0.5% round-trip; aim for >1% edge per trade.

Test in Kalshi demo (change URL). Monitor logs for issues. This is a starting point—adapt based on performance.
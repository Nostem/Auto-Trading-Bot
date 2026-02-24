#!/bin/bash
set -e

echo "[entrypoint] Starting Kalshi Bot services…"

# Start FastAPI server
uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
API_PID=$!
echo "[entrypoint] FastAPI started (pid=$API_PID)"

# Start bot
python -m bot.main &
BOT_PID=$!
echo "[entrypoint] Bot started (pid=$BOT_PID)"

# Exit when either process dies
wait -n
EXIT_CODE=$?

echo "[entrypoint] A process exited with code $EXIT_CODE — shutting down"
kill $API_PID $BOT_PID 2>/dev/null || true
exit $EXIT_CODE

#!/bin/bash
set -e

MODE="${SERVICE_MODE:-all}"

echo "[entrypoint] Starting Kalshi Bot services (mode=${MODE})…"

if [ "$MODE" = "api" ]; then
  exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
fi

if [ "$MODE" = "worker" ]; then
  python -m bot.main &
  BOT_PID=$!

  python - <<'PY' &
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


port = int(os.getenv("PORT", "8000"))
HTTPServer(("0.0.0.0", port), Handler).serve_forever()
PY
  HEALTH_PID=$!

  shutdown() {
    kill "$BOT_PID" "$HEALTH_PID" 2>/dev/null || true
    wait "$BOT_PID" 2>/dev/null || true
    wait "$HEALTH_PID" 2>/dev/null || true
  }

  trap shutdown INT TERM

  wait -n "$BOT_PID" "$HEALTH_PID"
  STATUS=$?
  shutdown
  exit "$STATUS"
fi

if [ "$MODE" != "all" ]; then
  echo "[entrypoint] Invalid SERVICE_MODE=${MODE}. Use api, worker, or all."
  exit 2
fi

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

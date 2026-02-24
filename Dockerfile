# Multi-stage Python 3.11 Dockerfile for Kalshi Bot
# Stage 1: Install dependencies
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY bot/ ./bot/
COPY api/ ./api/
COPY db/ ./db/
COPY .env.example ./.env.example

# Copy entrypoint
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Railway injects PORT env var
ENV PORT=8000

EXPOSE $PORT

CMD ["./entrypoint.sh"]

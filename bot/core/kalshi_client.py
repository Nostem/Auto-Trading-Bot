"""
Kalshi API client — the single point of contact for all Kalshi REST API calls.
Handles HMAC-SHA256 authentication, rate limiting, retries, and logging.
"""
import asyncio
import base64
import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import httpx
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()
logger = logging.getLogger(__name__)


class KalshiAPIError(Exception):
    """Raised when the Kalshi API returns an error response."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Kalshi API error {status_code}: {message}")


class RateLimitError(KalshiAPIError):
    """Raised on HTTP 429 — signals tenacity to retry."""
    pass


class KalshiClient:
    """
    Async HTTP client for the Kalshi REST API v2.

    Usage:
        async with KalshiClient() as client:
            balance = await client.get_balance()
    """

    def __init__(self):
        self.api_key = os.getenv("KALSHI_API_KEY", "")
        self.api_secret = os.getenv("KALSHI_API_SECRET", "")
        self.base_url = os.getenv("KALSHI_BASE_URL", "https://trading-api.kalshi.com/trade-api/v2")
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "KalshiClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    def _sign_request(self, method: str, path: str) -> dict:
        """Build HMAC-SHA256 signed headers for Kalshi API authentication."""
        timestamp_ms = str(int(time.time() * 1000))
        # Signature = HMAC-SHA256(timestamp + METHOD + path)
        message = timestamp_ms + method.upper() + path
        secret_bytes = base64.b64decode(self.api_secret) if self.api_secret else b""
        signature = hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256).digest()
        sig_b64 = base64.b64encode(signature).decode("utf-8")
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "Content-Type": "application/json",
        }

    # -------------------------------------------------------------------------
    # Core request method
    # -------------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    @retry(
        retry=retry_if_exception_type(httpx.RequestError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=16),
    )
    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an authenticated request to the Kalshi API."""
        if not self._client:
            raise RuntimeError("KalshiClient must be used as an async context manager")

        headers = self._sign_request(method, path)
        logger.debug("Kalshi %s %s", method, path)

        response = await self._client.request(method, path, headers=headers, **kwargs)

        logger.debug("Kalshi response %s %s → %d", method, path, response.status_code)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            logger.warning("Kalshi rate limit hit on %s %s — waiting %ds", method, path, retry_after)
            await asyncio.sleep(retry_after)
            raise RateLimitError(429, "Rate limit exceeded")

        if response.status_code >= 400:
            try:
                body = response.json()
                message = body.get("error", response.text)
            except Exception:
                message = response.text
            raise KalshiAPIError(response.status_code, message)

        if response.status_code == 204 or not response.content:
            return {}

        return response.json()

    # -------------------------------------------------------------------------
    # API methods
    # -------------------------------------------------------------------------

    async def get_balance(self) -> dict:
        """Return available USDC balance."""
        return await self._request("GET", "/portfolio/balance")

    async def get_markets(
        self,
        status: str = "open",
        category: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch markets with optional filtering. Paginates automatically."""
        markets = []
        cursor = None

        while True:
            params: dict = {"status": status, "limit": min(limit, 200)}
            if category:
                params["category"] = category
            if cursor:
                params["cursor"] = cursor

            data = await self._request("GET", "/markets", params=params)
            batch = data.get("markets", [])
            markets.extend(batch)

            cursor = data.get("cursor")
            if not cursor or len(markets) >= limit:
                break

        return markets[:limit]

    async def get_market(self, ticker: str) -> dict:
        """Fetch a single market by ticker."""
        data = await self._request("GET", f"/markets/{ticker}")
        return data.get("market", data)

    async def get_orderbook(self, ticker: str) -> dict:
        """Fetch the order book for a market (yes/no bids and asks)."""
        data = await self._request("GET", f"/markets/{ticker}/orderbook")
        return data.get("orderbook", data)

    async def place_order(
        self,
        ticker: str,
        side: str,
        count: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        """
        Place an order on Kalshi.

        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            count: Number of contracts
            price: Price in cents (0–100)
            order_type: 'limit' or 'market'
        """
        payload = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": count,
            "type": order_type,
        }
        if order_type == "limit":
            payload["yes_price"] = price if side == "yes" else 100 - price

        logger.info("Placing %s order: %s %s @ %d¢ x%d", order_type, ticker, side, price, count)
        return await self._request("POST", "/portfolio/orders", json=payload)

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by ID."""
        logger.info("Cancelling order %s", order_id)
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")

    async def get_orders(self, status: str = "open") -> list[dict]:
        """Fetch orders filtered by status."""
        data = await self._request("GET", "/portfolio/orders", params={"status": status})
        return data.get("orders", [])

    async def get_positions(self) -> list[dict]:
        """Fetch all open positions."""
        data = await self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    async def get_fills(self, ticker: Optional[str] = None) -> list[dict]:
        """Fetch trade fills, optionally filtered by market ticker."""
        params = {}
        if ticker:
            params["ticker"] = ticker
        data = await self._request("GET", "/portfolio/fills", params=params)
        return data.get("fills", [])


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def main():
        async with KalshiClient() as client:
            balance = await client.get_balance()
            print("Balance:", balance)

    asyncio.run(main())

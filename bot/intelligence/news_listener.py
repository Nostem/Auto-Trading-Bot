"""
News Listener — polls RSS feeds and classifies headlines using Claude
for trading relevance before dispatching to the news arbitrage strategy.
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import feedparser
import anthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://rss.app/feeds/politics.xml",
    "https://feeds.feedburner.com/coindesk/CoinDeskMain",
    "https://rss.politico.com/politics-news.xml",
    "https://feeds.bloomberg.com/markets/news.rss",
]

_CLASSIFY_SYSTEM = (
    "You are a prediction market trading assistant. "
    "Classify news headlines for their impact on Kalshi prediction markets. "
    "Return JSON only."
)

_CLASSIFY_USER_TEMPLATE = (
    'Headline: {title}\nSummary: {summary}\n\n'
    'Return JSON: {{"relevant": bool, '
    '"affected_categories": ["list of Kalshi categories like \'politics\', \'economics\', \'crypto\', \'sports\'"], '
    '"direction": "yes_up" or "no_up" or "neutral", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "one sentence"}}'
)


@dataclass
class ClassifiedHeadline:
    headline: str
    summary: str
    source: str
    published: datetime
    relevant: bool
    affected_categories: list[str]
    direction: str
    confidence: float
    reasoning: str


class NewsListener:
    """
    Monitors RSS feeds, deduplicates headlines, and uses Claude to classify
    each new headline for prediction-market trading relevance.
    """

    def __init__(self):
        self._seen_ids: set[str] = set()
        self._running = False
        self._claude = anthropic.AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "")
        )

    async def start_polling(
        self,
        callback: Callable,
        interval_seconds: int = 30,
    ) -> None:
        """
        Poll all feeds every interval_seconds. For each unseen headline,
        classify it and call callback(classified_headline).
        Runs indefinitely until stop() is called.
        """
        self._running = True
        logger.info("NewsListener: starting feed polling (interval=%ds)", interval_seconds)

        while self._running:
            try:
                await self._poll_once(callback)
            except Exception as exc:
                logger.error("NewsListener: poll error: %s", exc)
            await asyncio.sleep(interval_seconds)

    def stop(self):
        self._running = False

    async def _poll_once(self, callback: Callable) -> None:
        """Fetch all feeds and process new headlines."""
        tasks = [self.fetch_feed(url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_count = 0
        for items in results:
            if isinstance(items, Exception):
                continue
            for item in items:
                item_id = item.get("link") or item.get("guid") or item.get("title", "")
                if not item_id or item_id in self._seen_ids:
                    continue
                self._seen_ids.add(item_id)
                new_count += 1

                # Don't block the polling loop — classify in background
                asyncio.create_task(self._classify_and_dispatch(item, callback))

        if new_count:
            logger.debug("NewsListener: found %d new headline(s)", new_count)

    async def fetch_feed(self, url: str) -> list[dict]:
        """Fetch and parse an RSS feed. Returns list of item dicts."""
        try:
            loop = asyncio.get_event_loop()
            # feedparser is synchronous — run in thread pool
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            items = []
            for entry in feed.entries:
                items.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", entry.get("description", "")),
                    "link": entry.get("link", ""),
                    "guid": entry.get("id", ""),
                    "published": entry.get("published", ""),
                    "source": feed.feed.get("title", url),
                })
            return items
        except Exception as exc:
            logger.warning("NewsListener: failed to fetch %s: %s", url, exc)
            return []

    async def classify_headline(self, headline: dict) -> ClassifiedHeadline:
        """
        Use Claude (claude-haiku-4-5) to classify a headline for trading
        relevance. Returns a ClassifiedHeadline dataclass.
        """
        title = headline.get("title", "")
        summary = headline.get("summary", "")[:500]  # truncate long summaries
        source = headline.get("source", "")

        # Parse published date
        published_raw = headline.get("published", "")
        try:
            from email.utils import parsedate_to_datetime
            published = parsedate_to_datetime(published_raw)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except Exception:
            published = datetime.now(timezone.utc)

        prompt = _CLASSIFY_USER_TEMPLATE.format(title=title, summary=summary)

        try:
            response = await self._claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
                system=_CLASSIFY_SYSTEM,
            )
            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("NewsListener: failed to parse Claude JSON for '%s': %s", title, exc)
            data = {}
        except Exception as exc:
            logger.error("NewsListener: Claude API error classifying '%s': %s", title, exc)
            data = {}

        return ClassifiedHeadline(
            headline=title,
            summary=summary,
            source=source,
            published=published,
            relevant=bool(data.get("relevant", False)),
            affected_categories=data.get("affected_categories", []),
            direction=data.get("direction", "neutral"),
            confidence=float(data.get("confidence", 0.0)),
            reasoning=data.get("reasoning", ""),
        )

    async def _classify_and_dispatch(
        self,
        headline: dict,
        callback: Callable,
    ) -> None:
        """Classify a headline and call the callback if relevant."""
        try:
            classified = await self.classify_headline(headline)
            if classified.relevant and classified.confidence >= 0.5:
                logger.info(
                    "NewsListener: relevant headline '%s' (conf=%.2f, dir=%s)",
                    classified.headline[:80],
                    classified.confidence,
                    classified.direction,
                )
                await callback(classified)
        except Exception as exc:
            logger.error(
                "NewsListener: dispatch error for '%s': %s",
                headline.get("title", "?"), exc,
            )

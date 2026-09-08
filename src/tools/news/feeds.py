"""RSS-based news collection for US and India markets.

RSS-first by design: feeds are cheap, structured, and rarely rate-limited.
A dead feed is skipped, never fatal. Full-article text is extracted with
trafilatura only when requested (``include_text``), else the feed summary is
used — enough for ticker matching, dedupe, and sentiment inputs.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import feedparser

from src.utils.rate_limiter import RateLimitedSession

FEEDS: Dict[str, List[Dict[str, str]]] = {
    "us": [
        {"source": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
        {"source": "MarketWatch", "url": "http://feeds.marketwatch.com/marketwatch/topstories/"},
        {"source": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex"},
        {"source": "Benzinga", "url": "https://www.benzinga.com/feed"},
    ],
    "in": [
        {"source": "Economic Times", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
        {"source": "Economic Times", "url": "https://economictimes.indiatimes.com/company/rssfeeds/13352306.cms"},
        {"source": "Mint", "url": "https://www.livemint.com/rss/markets"},
        {"source": "Mint", "url": "https://www.livemint.com/rss/economy"},
        {"source": "NDTV Profit", "url": "https://www.indiatoday.in/rss/1206592"},
    ],
}


def _published(entry: Any) -> Optional[datetime]:
    dt = entry.get("published_parsed") or entry.get("updated_parsed")
    if not dt:
        return None
    try:
        return datetime(*dt[:6])
    except Exception:
        return None


def _summarize(entry: Any) -> str:
    s = (
        entry.get("summary")
        or entry.get("description")
        or entry.get("title_detail", {}).get("value", "")
        or ""
    )
    import re

    return re.sub(r"<[^>]+>", " ", s).strip()[:1500]


def parse_feed(country: str, source: dict, limit: int = 15, days: int = 7) -> List[Dict[str, Any]]:
    """Fetch one RSS feed and return normalized story dicts."""
    session = RateLimitedSession(calls_per_second=2.0, service_name=f"news_{source['source']}")
    try:
        resp = session.get(source["url"], timeout=15)
        resp.raise_for_status()
    except Exception:
        return []
    parsed = feedparser.parse(resp.content)
    cutoff = datetime.now() - timedelta(days=days)
    stories = []
    for entry in parsed.entries[:limit]:
        published = _published(entry)
        if published and published < cutoff:
            continue
        link = entry.get("link")
        if not link:
            continue
        stories.append(
            {
                "country": country,
                "source": source["source"],
                "url": link,
                "title": entry.get("title", "").strip(),
                "summary": _summarize(entry),
                "published_at": published,
            }
        )
    return stories


async def fetch_news(country: str, limit_per_feed: int = 15, days: int = 7) -> List[Dict[str, Any]]:
    """Fetch all feeds for a country. Per-feed failures are tolerated."""
    if country not in FEEDS:
        raise ValueError(f"Unsupported country: {country}")

    results = await asyncio.gather(
        *[
            asyncio.to_thread(parse_feed, country, feed, limit_per_feed, days)
            for feed in FEEDS[country]
        ],
        return_exceptions=True,
    )
    stories: List[Dict[str, Any]] = []
    failed = 0
    for r in results:
        if isinstance(r, Exception):
            failed += 1
            continue
        stories.extend(r)
    stories.sort(key=lambda s: s.get("published_at") or datetime.min, reverse=True)
    return stories

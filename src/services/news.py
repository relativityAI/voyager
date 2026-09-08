"""News service: fetch RSS stories, persist deduplicated, serve queries."""

import re
from datetime import datetime, timedelta
from typing import Any, Dict

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import NewsArticle
from src.tools.news.feeds import fetch_news

from ._common import InvalidRequestError

_COUNTRY_FRESHNESS = timedelta(hours=4)


def _matches(article: Dict[str, Any], pattern: str) -> bool:
    haystack = f"{article.get('title', '')} {article.get('summary', '')}"
    return bool(re.search(pattern, haystack, re.IGNORECASE))


async def _refresh_if_stale(country: str, days: int) -> bool:
    """Fetch + upsert new stories if the last fetch is older than freshness."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(NewsArticle)
            .where(NewsArticle.country == country)
            .order_by(NewsArticle.fetched_at.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()

    if latest and (datetime.now() - latest.fetched_at) < _COUNTRY_FRESHNESS:
        return False

    try:
        stories = await fetch_news(country, days=days)
    except Exception:
        logger.exception(f"News fetch failed for {country}; serving stored stories")
        return False

    if not stories:
        return False

    rows = [
        {
            "country": s["country"],
            "source": s["source"],
            "url": s["url"],
            "title": s.get("title"),
            "summary": s.get("summary"),
            "published_at": s.get("published_at"),
            "fetched_at": datetime.now(),
        }
        for s in stories
    ]
    async with factory() as session:
        for row in rows:
            stmt = (
                pg_insert(NewsArticle)
                .values(**row)
                .on_conflict_do_nothing(index_elements=["url"])
            )
            await session.execute(stmt)
        await session.commit()
    return True


async def get_news_stories(
    country: str = "in",
    days: int = 7,
    limit: int = 20,
    query: str = None,
) -> Dict[str, Any]:
    country = country.lower()
    if country not in ("us", "in"):
        raise InvalidRequestError("country must be 'us' or 'in'")
    if limit < 1 or limit > 50:
        raise InvalidRequestError("limit must be between 1 and 50")

    await _refresh_if_stale(country, days)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(NewsArticle)
            .where(
                NewsArticle.country == country,
                NewsArticle.published_at
                >= (datetime.now() - timedelta(days=days)),
            )
            .order_by(NewsArticle.published_at.desc().nulls_last())
            .limit(limit)
        )
        docs = result.scalars().all()

    stories = [
        {
            "title": d.title,
            "source": d.source,
            "url": d.url,
            "summary": d.summary or "",
            "published_at": d.published_at.isoformat() if d.published_at else None,
        }
        for d in docs
    ]
    return {
        "country": country,
        "days": days,
        "count": len(stories),
        "stories": stories,
    }


async def get_ticker_mentions(
    symbol: str,
    country: str = "in",
    days: int = 7,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    country = country.lower()
    if country not in ("us", "in"):
        raise InvalidRequestError("country must be 'us' or 'in'")

    await _refresh_if_stale(country, days)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(NewsArticle)
            .where(
                NewsArticle.country == country,
                NewsArticle.published_at
                >= (datetime.now() - timedelta(days=days)),
            )
            .order_by(NewsArticle.published_at.desc().nulls_last())
        )
        docs = result.scalars().all()

    pattern = rf"(?<![A-Za-z]){re.escape(symbol)}(?![A-Za-z])"
    matches = [
        {
            "title": d.title,
            "source": d.source,
            "url": d.url,
            "summary": d.summary or "",
            "published_at": d.published_at.isoformat() if d.published_at else None,
        }
        for d in docs
        if _matches({"title": d.title, "summary": d.summary or ""}, pattern)
    ]
    return {
        "symbol": symbol,
        "country": country,
        "days": days,
        "count": len(matches),
        "stories": matches,
    }

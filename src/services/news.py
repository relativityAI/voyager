"""News service: serve stored RSS stories (DB-only).

Fetching + persisting stories lives in ``src.ingest.news`` and runs via the
``voyager ingest news`` CLI.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy import select

from src.db.engine import get_session_factory
from src.db.models import MacroNews, StockNews

from ._common import InvalidRequestError


def _story_dict(d) -> dict:
    return {
        "title": d.title,
        "source": d.source,
        "url": d.url,
        "summary": d.summary or "",
        "published_at": d.published_at.isoformat() if d.published_at else None,
    }


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

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(MacroNews)
            .where(
                MacroNews.country == country,
                MacroNews.published_at
                >= (datetime.now() - timedelta(days=days)),
            )
            .order_by(MacroNews.published_at.desc().nulls_last())
            .limit(limit)
        )
        docs = result.scalars().all()

    stories = [_story_dict(d) for d in docs]
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

    pattern = re.compile(rf"(?<![A-Za-z]){re.escape(symbol)}(?![A-Za-z])")
    since = datetime.now() - timedelta(days=days)

    factory = get_session_factory()
    async with factory() as session:
        # Symbol-tagged news (stock_news) matches the symbol column directly.
        stock_rows = (
            await session.execute(
                select(StockNews).where(
                    StockNews.symbol == symbol,
                    StockNews.country == country,
                    StockNews.published_at >= since,
                ).order_by(StockNews.published_at.desc().nulls_last()).limit(50)
            )
        ).scalars().all()

        # Market news (macro_news) that mention the symbol in title/summary.
        macro_rows = (
            await session.execute(
                select(MacroNews).where(
                    MacroNews.country == country,
                    MacroNews.published_at >= since,
                ).order_by(MacroNews.published_at.desc().nulls_last()).limit(200)
            )
        ).scalars().all()

    seen = set()
    matches = []
    for d in list(stock_rows) + list(macro_rows):
        if d.url in seen:
            continue
        payload = {
            "title": d.title,
            "summary": d.summary or "",
            "url": d.url,
        }
        if not pattern.search(f"{payload['title']} {payload['summary']}"):
            continue
        seen.add(d.url)
        matches.append(_story_dict(d))

    matches.sort(key=lambda s: s["published_at"] or "", reverse=True)
    return {
        "symbol": symbol,
        "country": country,
        "days": days,
        "count": len(matches),
        "stories": matches[:50],
    }

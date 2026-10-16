"""News ingestion: fetch RSS stories and persist them (CLI/cron path)."""

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import NewsArticle
from src.tools.news.feeds import fetch_news

_COUNTRY_FRESHNESS = timedelta(hours=4)


async def refresh_news(country: str, days: int = 7) -> bool:
    """Fetch + upsert new stories; skips when the country was fetched recently.

    ponytail: no backoff/retry — cron re-runs; a failed fetch just means the
    next run picks it up.
    """
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
        logger.exception(f"News fetch failed for {country}")
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

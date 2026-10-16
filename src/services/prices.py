"""Price history reads (OHLCV). Downloads live in ``src.ingest.prices``."""

from datetime import date
from typing import Optional

from sqlalchemy import select, and_

from src.db.engine import get_session_factory
from src.db.models import PriceOHLCV
from src.services._common import NotFoundError


def _resample_bars(rows: list, interval: str) -> list[dict]:
    if not rows or interval == "daily":
        return [
            {
                "date": r.date.isoformat(),
                "open": float(r.open) if r.open is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "close": float(r.close) if r.close is not None else None,
                "adjusted_close": float(r.adjusted_close) if r.adjusted_close is not None else None,
                "volume": r.volume,
            }
            for r in rows
        ]

    from collections import OrderedDict
    buckets: OrderedDict[str, list] = OrderedDict()

    for r in rows:
        if interval == "weekly":
            iso_year, iso_week, _ = r.date.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        else:
            key = f"{r.date.year}-{r.date.month:02d}"
        buckets.setdefault(key, []).append(r)

    resampled = []
    for key, bucket_rows in buckets.items():
        resampled.append({
            "date": bucket_rows[-1].date.isoformat(),
            "open": float(bucket_rows[0].open) if bucket_rows[0].open is not None else None,
            "high": max(float(r.high) for r in bucket_rows if r.high is not None) if any(r.high is not None for r in bucket_rows) else None,
            "low": min(float(r.low) for r in bucket_rows if r.low is not None) if any(r.low is not None for r in bucket_rows) else None,
            "close": float(bucket_rows[-1].close) if bucket_rows[-1].close is not None else None,
            "adjusted_close": float(bucket_rows[-1].adjusted_close) if bucket_rows[-1].adjusted_close is not None else None,
            "volume": sum(r.volume for r in bucket_rows if r.volume is not None) if any(r.volume is not None for r in bucket_rows) else None,
        })

    return resampled


async def get_price_history(
    symbol: str,
    exchange: str,
    country: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    interval: str = "daily",
    limit: int = 500,
    offset: int = 0,
) -> dict:
    symbol = symbol.upper()
    exchange = exchange.upper()

    async with get_session_factory()() as session:
        conditions = [
            PriceOHLCV.symbol == symbol,
            PriceOHLCV.exchange == exchange,
        ]
        if country:
            conditions.append(PriceOHLCV.country == country.upper())
        if start_date:
            conditions.append(PriceOHLCV.date >= date.fromisoformat(start_date))
        if end_date:
            conditions.append(PriceOHLCV.date <= date.fromisoformat(end_date))

        from sqlalchemy import func as sql_func
        count_stmt = select(sql_func.count(PriceOHLCV.id)).where(and_(*conditions))
        total = await session.scalar(count_stmt)

        stmt = (
            select(PriceOHLCV)
            .where(and_(*conditions))
            .order_by(PriceOHLCV.date)
        )
        result = await session.scalars(stmt)
        rows = result.all()

    if not rows:
        raise NotFoundError(f"No price data for {symbol}.{exchange}")

    resampled = _resample_bars(rows, interval)
    total_resampled = len(resampled)

    if offset >= total_resampled:
        resampled_page = []
    else:
        resampled_page = resampled[offset:offset + limit]

    return {
        "symbol": symbol,
        "exchange": exchange,
        "country": country.upper() if country else None,
        "interval": interval,
        "total": total_resampled,
        "limit": limit,
        "offset": offset,
        "data": resampled_page,
    }

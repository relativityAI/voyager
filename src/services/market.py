"""Market data service — fetch, store, and serve price/volume data.

NSE is the primary source (quote-equity + historical); yfinance is the
fallback.  History depth: 3 years (S-06).  This module never modifies the
existing pull flow; it is a standalone service called by the pull job and
the API endpoint.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import MarketDataPoint

_HISTORICAL_DEPTH_YEARS = 3
_YF_SUFFIX_MAP = {"NSE": ".NS", "BSE": ".BO"}


def _parse_date(d: Any) -> Optional[date]:
    if isinstance(d, date):
        return d
    if isinstance(d, datetime):
        return d.date()
    try:
        return datetime.strptime(str(d), "%d-%b-%Y").date()
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def pull_market_data(symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
    """Fetch quote + 3-year daily history and persist to ``market_data``.

    Returns a summary dict.  Never raises — all errors are caught and
    surfaced in the result so the caller (pull job) is not broken.
    """
    symbol = symbol.upper()
    result: Dict[str, Any] = {"symbol": symbol, "exchange": exchange, "rows": 0, "source": "nse"}

    try:
        from src.tools.nse.client import NSEIndia
        from src.tools.nse.market import fetch_quote, fetch_historical, _parse_date as _mparse

        nse = NSEIndia()
        quote = await asyncio.to_thread(fetch_quote, nse.api, symbol)
        if quote:
            result["quote"] = quote
        else:
            result["quote"] = {}

        hist_rows = await asyncio.to_thread(fetch_historical, nse.api, symbol)
    except Exception as exc:
        logger.warning(f"NSE market fetch failed for {symbol}: {exc}; falling back to yfinance")
        hist_rows = []
        result["quote"] = {}

    if not hist_rows:
        hist_rows = await _yfinance_history(symbol, exchange)
        if hist_rows:
            result["source"] = "yfinance"
        else:
            result["source"] = "none"
            return result

    rows = []
    for r in hist_rows:
        td = _parse_date(r.get("trade_date"))
        if td is None:
            continue
        rows.append({
            "symbol": symbol,
            "exchange": exchange,
            "trade_date": td,
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "adjusted_close": r.get("close"),
            "volume": int(r["volume"]) if r.get("volume") else None,
            "delivery_percentage": r.get("delivery_pct"),
            "turnover": r.get("turnover"),
            "source": result["source"],
            "interval": "EOD",
            "fetched_at": datetime.utcnow(),
        })

    factory = get_session_factory()
    async with factory() as session:
        for row in rows:
            stmt = (
                pg_insert(MarketDataPoint)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["symbol", "exchange", "trade_date", "source", "interval"],
                    set_={
                        k: v
                        for k, v in row.items()
                        if k not in ("symbol", "exchange", "trade_date", "source", "interval")
                    },
                )
            )
            await session.execute(stmt)
        await session.commit()

    result["rows"] = len(rows)
    return result


async def _yfinance_history(symbol: str, exchange: str) -> List[Dict[str, Any]]:
    """Fallback: fetch daily OHLCV from yfinance."""
    try:
        import yfinance as yf
        suffix = _YF_SUFFIX_MAP.get(exchange.upper(), f".{exchange.upper()}")
        ticker = yf.Ticker(symbol + suffix)
        hist = ticker.history(period="3y")
        if hist.empty:
            return []
        rows = []
        for idx, row in hist.iterrows():
            td = idx.date() if hasattr(idx, "date") else idx
            rows.append({
                "trade_date": str(td),
                "open": row.get("Open"),
                "high": row.get("High"),
                "low": row.get("Low"),
                "close": row.get("Close"),
                "volume": row.get("Volume"),
            })
        return rows
    except Exception as exc:
        logger.debug(f"yfinance history failed for {symbol}: {exc}")
        return []


async def get_market_data(
    symbol: str,
    exchange: str = "NSE",
    limit: int = 0,
) -> Dict[str, Any]:
    """Read stored market data for a symbol, newest first.

    ``limit=0`` returns all rows.
    """
    symbol = symbol.upper()
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(MarketDataPoint)
            .where(
                MarketDataPoint.symbol == symbol,
                MarketDataPoint.exchange == exchange,
            )
            .order_by(MarketDataPoint.trade_date.desc())
        )
        if limit > 0:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        return {"symbol": symbol, "exchange": exchange, "history": []}

    return {
        "symbol": symbol,
        "exchange": exchange,
        "history": [r.to_dict() for r in rows],
    }


async def get_latest_quote(symbol: str, exchange: str = "NSE") -> Optional[Dict[str, Any]]:
    """Return the most recent stored row for a symbol (or None)."""
    symbol = symbol.upper()
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(MarketDataPoint)
            .where(
                MarketDataPoint.symbol == symbol,
                MarketDataPoint.exchange == exchange,
            )
            .order_by(MarketDataPoint.trade_date.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
    return row.to_dict() if row else None


__all__ = [
    "get_latest_quote",
    "get_market_data",
    "pull_market_data",
]
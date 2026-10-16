"""Macro ingestion: pull NSE/RBI macro data and store in the macro_data table.

Write path only — invoked by the operator tooling, never by the serving API.
Reads live in ``src.services.macro``. Each pull stores the service-layer
output (same shape the API returns) so the service can serve it from cache.
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text

from src.db.engine import get_session_factory
from src.db.models import DataCatalog, MacroData
from src.services.macro import (
    macro_breadth,
    macro_derivatives,
    macro_flows,
    macro_indices,
    macro_index_constituents,
    macro_index_history,
    macro_index_quote,
    macro_index_returns,
    macro_index_valuation,
    macro_market_valuation,
    macro_overview,
    macro_rates,
    macro_turnover,
)
from src.services._common import _validate_source


async def _store_macro(
    category: str,
    data: Dict[str, Any],
    symbol: Optional[str] = None,
    source: str = "nse",
) -> None:
    """Upsert into macro_data (one row per category+symbol+source)."""
    import json

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO macro_data (category, symbol, source, data, fetched_at)
                VALUES (:category, :symbol, :source, CAST(:data AS JSONB), :fetched_at)
                ON CONFLICT (category, symbol, source)
                DO UPDATE SET data = CAST(:data AS JSONB), fetched_at = :fetched_at
                """
            ),
            {
                "category": category,
                "symbol": symbol,
                "source": source,
                "data": json.dumps(data),
                "fetched_at": datetime.utcnow(),
            },
        )
        await session.commit()
    await _update_catalog(symbol or "MACRO", category, source)


async def _update_catalog(symbol: str, category: str, source: str) -> None:
    """Record macro pull in the data_catalog ledger."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO data_catalog (symbol, exchange, country, total_records, last_updated)
                VALUES (:symbol, :exchange, 'IN', 1, :now)
                ON CONFLICT (symbol, exchange, country)
                DO UPDATE SET total_records = data_catalog.total_records + 1,
                              last_updated = :now
                """
            ),
            {
                "symbol": symbol,
                "exchange": "NSE" if source == "nse" else "SEC",
                "now": datetime.utcnow(),
            },
        )
        await session.commit()


def _describe(category: str, data: Dict[str, Any]) -> str:
    count = data.get("count")
    if count is not None:
        return f"[green]{count} records[/green]"
    if "indices" in data:
        return f"[green]{len(data['indices'])} indices[/green]"
    if "rates" in data:
        return "[green]rates[/green]"
    if "turnover" in data:
        return f"[green]{len(data['turnover'])} segments[/green]"
    if "fii_dii" in data:
        return f"[green]{len(data['fii_dii'])} flow records[/green]"
    return "[green]stored[/green]"


async def pull_macro_overview() -> Dict[str, Any]:
    data = await macro_overview("in")
    await _store_macro("overview", data)
    return {"category": "overview", "status": "completed", "detail": _describe("overview", data)}


async def pull_macro_indices(limit: Optional[int] = None) -> Dict[str, Any]:
    data = await macro_indices("in", limit)
    await _store_macro("indices", data)
    return {"category": "indices", "status": "completed", "detail": _describe("indices", data)}


async def pull_macro_index_quote(symbol: str) -> Dict[str, Any]:
    data = await macro_index_quote("in", symbol)
    await _store_macro("index_quote", data, symbol=symbol)
    return {"category": "index_quote", "symbol": symbol, "status": "completed"}


async def pull_macro_index_history(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    data = await macro_index_history("in", symbol, start_date, end_date)
    await _store_macro("index_history", data, symbol=symbol)
    return {"category": "index_history", "symbol": symbol, "status": "completed", "detail": f"{len(data.get('bars', []))} bars"}


async def pull_macro_index_valuation(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    data = await macro_index_valuation("in", symbol, start_date, end_date)
    await _store_macro("index_valuation", data, symbol=symbol)
    return {"category": "index_valuation", "symbol": symbol, "status": "completed"}


async def pull_macro_index_returns(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    data = await macro_index_returns("in", symbol, start_date, end_date)
    await _store_macro("index_returns", data, symbol=symbol)
    return {"category": "index_returns", "symbol": symbol, "status": "completed"}


async def pull_macro_index_constituents(symbol: str) -> Dict[str, Any]:
    data = await macro_index_constituents("in", symbol)
    await _store_macro("index_constituents", data, symbol=symbol)
    return {"category": "index_constituents", "symbol": symbol, "status": "completed", "detail": f"{data.get('count', 0)} constituents"}


async def pull_macro_market_valuation() -> Dict[str, Any]:
    data = await macro_market_valuation("in", None)
    await _store_macro("market_valuation", data)
    return {"category": "market_valuation", "status": "completed", "detail": f"{data.get('count', 0)} stocks"}


async def pull_macro_breadth(limit: int = 10) -> Dict[str, Any]:
    data = await macro_breadth("in", limit)
    await _store_macro("breadth", data)
    return {"category": "breadth", "status": "completed"}


async def pull_macro_flows() -> Dict[str, Any]:
    data = await macro_flows("in")
    await _store_macro("flows", data)
    return {"category": "flows", "status": "completed", "detail": _describe("flows", data)}


async def pull_macro_turnover() -> Dict[str, Any]:
    data = await macro_turnover("in")
    await _store_macro("turnover", data)
    return {"category": "turnover", "status": "completed", "detail": _describe("turnover", data)}


async def pull_macro_derivatives(symbol: str) -> Dict[str, Any]:
    data = await macro_derivatives("in", symbol)
    await _store_macro("derivatives", data, symbol=symbol)
    return {"category": "derivatives", "symbol": symbol, "status": "completed"}


async def pull_macro_rates() -> Dict[str, Any]:
    data = await macro_rates("in")
    await _store_macro("rates", data)
    return {"category": "rates", "status": "completed", "detail": _describe("rates", data)}

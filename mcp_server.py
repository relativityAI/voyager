from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, datetime
from typing import Any, Dict

from fastmcp import FastMCP
from loguru import logger

from __version__ import __version__
from src.core import (
    extract_pdf_content,
    fetch_marketsmithindia_data,
    fetch_nse_announcements,
    fetch_nse_annual_reports,
    fetch_nse_financials,
    fetch_nse_shareholdings,
    fetch_screener_data,
    fetch_screener_screen,
    fetch_stockscans_data,
    fetch_trendlyne_data,
)
from sqlalchemy import select

from src.db.connection import init_db
from src.db.engine import get_session_factory
from src.db.models import NSEAnnouncement, NSEAnnualReport
from src.logging_config import setup_logging
from src.models import SOURCE_MODELS
from src.services import (
    ServiceError,
    get_announcements,
    get_pull_status,
    get_shareholdings,
    get_statement_data,
    list_category,
)
from src.services import (
    financial_metrics as get_metrics,
)
from src.services import (
    get_financials as get_merged_financials,
)

# No file sink: loguru writes a JSON log into the project's logs/ dir, which the
# fastmcp dev-server file watcher sees and treats as a change -> restart loop.
setup_logging(file_sink=False)

_db_ready = False


async def _ensure_db() -> None:
    """Initialise the database once per process, lazily, on first DB-backed call."""
    global _db_ready
    if not _db_ready:
        try:
            await init_db()
            _db_ready = True
        except Exception as e:
            logger.warning(f"Database unavailable; DB-backed tools will fail: {e}")


mcp = FastMCP("Voyager", version=__version__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jsonable(obj: Any) -> Any:
    """Recursively convert a service result into JSON-serializable values."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def _to_json_text(data: Any) -> str:
    return json.dumps(_jsonable(data), indent=2, default=str)


async def _call(fn, *args, sync: bool = False, **kwargs) -> str:
    """Run a service/utility call, mapping ServiceError to a client-friendly error."""
    try:
        if sync:
            result = await asyncio.to_thread(fn, *args, **kwargs)
        else:
            result = await fn(*args, **kwargs)
    except ServiceError as e:
        raise RuntimeError(f"{type(e).__name__}: {e.message}") from e
    return _to_json_text(result)



async def _db_session():
    """Get an async SQLAlchemy session."""
    factory = get_session_factory()
    return factory()


# ---------------------------------------------------------------------------
# API-equivalent tools
# ---------------------------------------------------------------------------


@mcp.tool
async def ping() -> str:
    """Health check for the Voyager MCP server."""
    return _to_json_text({"ok": 1, "name": "Voyager", "version": __version__})


@mcp.tool
async def version() -> str:
    """Show the Voyager version."""
    return _to_json_text({"name": "Voyager", "version": __version__})


@mcp.tool
async def list_categories(
    category: str = "sources",
    country: str = "in",
    source: str = "nse",
) -> str:
    """List available categories: sources, countries, industries, sectors, indices."""
    return await _call(list_category, category, country, source, sync=True)


@mcp.tool
async def get_financials(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
    all_fields: bool = False,
) -> str:
    """Merged income + balance + cash-flow for the latest period of a stock."""
    await _ensure_db()
    return await _call(
        get_merged_financials,
        symbol,
        country,
        source,
        consolidated,
        filing_type,
        all_fields,
    )


@mcp.tool
async def get_income_statements(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
    limit: int = 0,
    all_fields: bool = False,
) -> str:
    """Income statements from the DB for a stock."""
    await _ensure_db()
    return await _call(
        get_statement_data,
        "income-statements",
        symbol,
        country,
        source,
        consolidated,
        filing_type,
        limit,
        all_fields,
    )


@mcp.tool
async def get_balance_sheets(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
    limit: int = 0,
    all_fields: bool = False,
) -> str:
    """Balance sheets from the DB for a stock."""
    await _ensure_db()
    return await _call(
        get_statement_data,
        "balance-sheets",
        symbol,
        country,
        source,
        consolidated,
        filing_type,
        limit,
        all_fields,
    )


@mcp.tool
async def get_cash_flows(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
    limit: int = 0,
    all_fields: bool = False,
) -> str:
    """Cash flow statements from the DB for a stock."""
    await _ensure_db()
    return await _call(
        get_statement_data,
        "cash-flows",
        symbol,
        country,
        source,
        consolidated,
        filing_type,
        limit,
        all_fields,
    )


@mcp.tool
async def pull_status(
    symbol: str,
    country: str = "in",
    source: str = "nse",
) -> str:
    """Pull history and data availability for a stock."""
    await _ensure_db()
    return await _call(get_pull_status, symbol, country, source)


@mcp.tool
async def get_financial_metrics(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
) -> str:
    """Computed financial metrics for a stock.

    filing_type is quarterly, annual, or ttm (trailing twelve months). This is
    Voyager's core endpoint: valuation, profitability, growth, solvency,
    per-share and market data.
    """
    await _ensure_db()
    return await _call(get_metrics, symbol, country, source, consolidated, filing_type)


@mcp.tool
async def announcements(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    market: str = "equities",
) -> str:
    """Corporate announcements for a stock (market: equities or sme)."""
    return await _call(get_announcements, symbol, country, source, market)


@mcp.tool
async def shareholdings(
    symbol: str,
    country: str = "in",
    source: str = "nse",
) -> str:
    """Shareholding pattern for a stock (promoter/FII/DII/public)."""
    await _ensure_db()
    return await _call(get_shareholdings, symbol, country, source)


# ---------------------------------------------------------------------------
# CLI-only NSE / PDF tools
# ---------------------------------------------------------------------------


@mcp.tool
async def nse_financials_raw(symbol: str) -> str:
    """Fetch and parse raw NSE financial XBRL filings (no DB write)."""
    return await _call(fetch_nse_financials, symbol, sync=True)


@mcp.tool
async def nse_announcements(symbol: str, save: bool = False) -> str:
    """Fetch raw NSE announcements; optionally save them to the DB."""
    results = await asyncio.to_thread(fetch_nse_announcements, symbol)
    if save:
        await _ensure_db()
        async with get_session_factory()() as session:
            for item in results:
                ann = NSEAnnouncement(
                    symbol=symbol,
                    an_dt=item.get("an_dt"),
                    attchmnt_text=item.get("attchmntText"),
                    desc=item.get("desc"),
                    attchmnt_file=item.get("attchmntFile"),
                    att_file_size=item.get("attFileSize"),
                    has_xbrl=item.get("hasXbrl", False),
                    sort_date=item.get("sort_date"),
                    raw_data=item,
                )
                session.add(ann)
            await session.commit()
    return _to_json_text(results)


@mcp.tool
async def nse_announcements_search(
    symbol: str,
    keywords: str = "transcript",
    cutoff_date: str = "2026-01-01",
) -> str:
    """Search announcements stored in the DB by keyword in the attachment text."""
    await _ensure_db()
    async with get_session_factory()() as session:
        result = await session.execute(
            select(NSEAnnouncement).where(
                NSEAnnouncement.symbol == symbol,
                NSEAnnouncement.attchmnt_text.ilike(f"%{keywords}%"),
                NSEAnnouncement.sort_date <= cutoff_date,
            )
        )
        docs = [dict(r._mapping) for r in result]
    return _to_json_text(docs)


@mcp.tool
async def nse_announcements_extract(path_or_url: str) -> str:
    """Extract text content of a stored announcement PDF (path or URL)."""
    await _ensure_db()
    async with get_session_factory()() as session:
        result = await session.execute(
            select(NSEAnnouncement).where(NSEAnnouncement.attchmnt_file == path_or_url)
        )
        data = result.scalars().first()
    if data is None:
        raise RuntimeError(f"NotFoundError: No document found in DB for {path_or_url}")
    text = await asyncio.to_thread(extract_pdf_content, path_or_url)
    return _to_json_text({"path_or_url": path_or_url, "content": text})


@mcp.tool
async def nse_annual_reports_list(symbol: str) -> str:
    """List annual reports for a symbol stored in the DB."""
    await _ensure_db()
    async with get_session_factory()() as session:
        result = await session.execute(
            select(NSEAnnualReport).where(NSEAnnualReport.symbol == symbol)
        )
        docs = [dict(r._mapping) for r in result]
    return _to_json_text(docs)


@mcp.tool
async def nse_annual_reports(symbol: str, save: bool = False) -> str:
    """Fetch annual report metadata from NSE; optionally save to the DB."""
    results = await asyncio.to_thread(fetch_nse_annual_reports, symbol)
    if save:
        await _ensure_db()
        async with get_session_factory()() as session:
            for item in results:
                report = NSEAnnualReport(
                    symbol=symbol,
                    file_name=item.get("fileName"),
                    raw_data=item,
                )
                session.add(report)
            await session.commit()
    return _to_json_text(results)


@mcp.tool
async def nse_shareholdings_raw(symbol: str) -> str:
    """Fetch and parse raw NSE shareholding XBRL filings (no DB write)."""
    return await _call(fetch_nse_shareholdings, symbol, sync=True)


@mcp.tool
async def get_source_schema(source: str) -> str:
    """Return the response model JSON schema for a data source."""
    model = SOURCE_MODELS.get(source.lower())
    if not model:
        available = ", ".join(sorted(SOURCE_MODELS))
        raise RuntimeError(
            f"InvalidRequestError: No model found for source '{source}'. "
            f"Available: {available}"
        )
    return _to_json_text(model.model_json_schema())


@mcp.tool
async def nse_full_download(symbol: str) -> str:
    """Run the full legacy NSE scrape: financials, announcements, shareholdings, annual reports."""
    await asyncio.to_thread(fetch_nse_financials, symbol)
    await asyncio.to_thread(fetch_nse_announcements, symbol)
    await asyncio.to_thread(fetch_nse_shareholdings, symbol)
    await asyncio.to_thread(fetch_nse_annual_reports, symbol)
    return _to_json_text({"symbol": symbol, "status": "completed"})


# ---------------------------------------------------------------------------
# Web screener tools
# ---------------------------------------------------------------------------


@mcp.tool
async def screener_fetch(symbol: str) -> str:
    """Fetch profile data for a stock from Screener.in."""
    return await _call(fetch_screener_data, symbol, sync=True)


@mcp.tool
async def screener_screen(url: str) -> str:
    """Fetch results from a custom Screener.in screener URL."""
    return await _call(fetch_screener_screen, url, sync=True)


@mcp.tool
async def trendlyne_fetch(symbol: str) -> str:
    """Fetch data for a symbol from Trendlyne."""
    return await _call(fetch_trendlyne_data, symbol, sync=True)


@mcp.tool
async def stockscans_fetch(url: str, payload: Dict[str, Any]) -> str:
    """Fetch scan results from StockScans given a URL and request payload."""
    return await _call(fetch_stockscans_data, url, payload, sync=True)


@mcp.tool
async def marketsmithindia_fetch(symbol: str) -> str:
    """Fetch data for a symbol from MarketSmith India."""
    return await _call(fetch_marketsmithindia_data, symbol, sync=True)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("voyager://schema/{source}")
def source_schema(source: str) -> str:
    """JSON schema for a data source's response model."""
    model = SOURCE_MODELS.get(source.lower())
    if not model:
        return _to_json_text({"error": f"No model found for source '{source}'"})
    return _to_json_text(model.model_json_schema())


@mcp.resource("voyager://list/{category}")
def category_list(category: str) -> str:
    """Static listing for a category (sources, countries, ...)."""
    try:
        return _to_json_text(list_category(category))
    except ServiceError as e:
        return _to_json_text({"error": e.message})


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt
def analyze_stock(symbol: str, filing_type: str = "ttm") -> str:
    """Chain Voyager tools to produce a fundamental analysis of a stock."""
    return (
        f"Analyze the stock {symbol} using the Voyager MCP tools. "
        f"1) Call get_financial_metrics with filing_type={filing_type} for "
        "valuation, profitability, growth and solvency. "
        "2) Call get_financials for the latest merged income/balance/cash-flow. "
        "3) Call shareholdings for the promoter/FII/DII ownership pattern. "
        "4) Call announcements for recent corporate events. "
        "Then summarize the key metrics, highlight risks, and give a balanced "
        f"fundamental view of {symbol}."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Voyager MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="MCP transport: stdio for local tools, http for Streamable HTTP.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "127.0.0.1"),
        help="Host to bind for the http transport.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8002")),
        help="Port to bind for the http transport.",
    )
    args = parser.parse_args()

    if args.transport == "http":
        logger.info(f"Voyager MCP HTTP server on http://{args.host}:{args.port}/mcp")
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()

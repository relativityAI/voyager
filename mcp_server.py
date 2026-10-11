"""MCP server exposing Voyager service layer as tools.

Single source of truth: src/services/. FastAPI routes and MCP tools both
call the same functions — edit services once, both interfaces update.
"""
import os

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

from src.logging_config import setup_logging
from src.services import (
    financial_metrics,
    get_announcements,
    get_financials,
    get_news_stories,
    get_reddit,
    get_shareholdings,
    get_statement_data,
    get_ticker_mentions,
    get_youtube_search,
    get_youtube_transcript,
    list_category,
    macro_breadth,
    macro_derivatives,
    macro_flows,
    macro_flows_fpi,
    macro_index_constituents,
    macro_index_history,
    macro_index_quote,
    macro_index_returns,
    macro_index_valuation,
    macro_indices,
    macro_market_valuation,
    macro_overview,
    macro_rates,
    macro_turnover,
    pull_nse_data,
    pull_sec_data,
    search_symbols,
)
from src.services.documents import read_document
from src.services.technical_report import build_technical_report

setup_logging()

mcp = FastMCP("voyager")


@mcp.tool()
async def search_symbols(
    q: str, source: str = "nse", limit: int = 20
) -> dict:
    """Search symbols stored in the DB (case-insensitive substring match).

    Args:
        q: Search text, e.g. 'relian'. Minimum 2 characters.
        source: 'nse' (NSE India, default) or 'sec' (US SEC/EDGAR).
        limit: Max results (1-50, default 20).
    """
    return await search_symbols(q, None, source, limit)


@mcp.tool()
def list_category(
    category: str = "sources", source: str = "nse"
) -> dict:
    """List available categories (sources, countries, industries, sectors, indices).

    Args:
        category: Category to enumerate.
        source: 'nse' (default) or 'sec'.
    """
    return list_category(category, source=source)


@mcp.tool()
async def get_financials(
    symbol: str,
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
    all_fields: bool = False,
    history: bool = False,
    report_period_gte: str | None = None,
    report_period_lte: str | None = None,
) -> dict:
    """Get merged financial data (income, balance, cash flow) for a stock.

    Args:
        symbol: Exchange ticker in UPPER CASE, e.g. 'RELIANCE'.
        source: 'nse' (default) or 'sec'.
        consolidated: Consolidated (default). Falls back to standalone for periods where only standalone was filed.
        filing_type: 'quarterly' or 'annual'.
        all_fields: Return all stored fields instead of only priority metrics.
        history: Add all stored periods merged per period.
        report_period_gte: Start period end date (YYYY-MM-DD).
        report_period_lte: End period end date (YYYY-MM-DD).
    """
    return await get_financials(
        symbol, None, source, consolidated, filing_type,
        all_fields, history, report_period_gte, report_period_lte,
    )


@mcp.tool()
async def get_income_statements(
    symbol: str,
    source: str = "nse",
    consolidated: bool | None = None,
    filing_type: str = "quarterly",
    limit: int = 0,
    offset: int = 0,
    all_fields: bool = False,
    report_period_gte: str | None = None,
    report_period_lte: str | None = None,
) -> dict:
    """Fetch income statement data from DB.

    Args:
        symbol: Exchange ticker in UPPER CASE.
        source: 'nse' (default) or 'sec'.
        consolidated: Both bases (default). True=consolidated only, False=standalone only.
        filing_type: 'quarterly' or 'annual'.
        limit: Max rows (0 = no limit).
        offset: Rows to skip (pagination).
        all_fields: Return all stored fields.
        report_period_gte: Start period end date (YYYY-MM-DD).
        report_period_lte: End period end date (YYYY-MM-DD).
    """
    return await get_statement_data(
        "income-statements", symbol, None, source, consolidated,
        filing_type, limit, offset, all_fields, report_period_gte, report_period_lte,
    )


@mcp.tool()
async def get_balance_sheets(
    symbol: str,
    source: str = "nse",
    consolidated: bool | None = None,
    filing_type: str = "quarterly",
    limit: int = 0,
    offset: int = 0,
    all_fields: bool = False,
    report_period_gte: str | None = None,
    report_period_lte: str | None = None,
) -> dict:
    """Fetch balance sheet data from DB.

    Args:
        symbol: Exchange ticker in UPPER CASE.
        source: 'nse' (default) or 'sec'.
        consolidated: Both bases (default). True=consolidated only, False=standalone only.
        filing_type: 'quarterly' or 'annual'.
        limit: Max rows (0 = no limit).
        offset: Rows to skip (pagination).
        all_fields: Return all stored fields.
        report_period_gte: Start period end date (YYYY-MM-DD).
        report_period_lte: End period end date (YYYY-MM-DD).
    """
    return await get_statement_data(
        "balance-sheets", symbol, None, source, consolidated,
        filing_type, limit, offset, all_fields, report_period_gte, report_period_lte,
    )


@mcp.tool()
async def get_cash_flows(
    symbol: str,
    source: str = "nse",
    consolidated: bool | None = None,
    filing_type: str = "quarterly",
    limit: int = 0,
    offset: int = 0,
    all_fields: bool = False,
    report_period_gte: str | None = None,
    report_period_lte: str | None = None,
) -> dict:
    """Fetch cash flow statement data from DB.

    Args:
        symbol: Exchange ticker in UPPER CASE.
        source: 'nse' (default) or 'sec'.
        consolidated: Both bases (default). True=consolidated only, False=standalone only.
        filing_type: 'quarterly' or 'annual'.
        limit: Max rows (0 = no limit).
        offset: Rows to skip (pagination).
        all_fields: Return all stored fields.
        report_period_gte: Start period end date (YYYY-MM-DD).
        report_period_lte: End period end date (YYYY-MM-DD).
    """
    return await get_statement_data(
        "cash-flows", symbol, None, source, consolidated,
        filing_type, limit, offset, all_fields, report_period_gte, report_period_lte,
    )


@mcp.tool()
async def financial_metrics(
    symbol: str,
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "ttm",
    report_period_gte: str | None = None,
    report_period_lte: str | None = None,
    limit: int | None = None,
) -> dict:
    """Computed ratios, margins and growth snapshot for a stock.

    Args:
        symbol: Exchange ticker in UPPER CASE.
        source: 'nse' (default) or 'sec'.
        consolidated: Consolidated (default).
        filing_type: 'quarterly', 'annual', or 'ttm' (trailing twelve months, default).
        report_period_gte: Start period end date (YYYY-MM-DD).
        report_period_lte: End period end date (YYYY-MM-DD).
        limit: Max periods to consider.
    """
    return await financial_metrics(
        symbol, None, source, consolidated, filing_type,
        report_period_gte, report_period_lte, limit,
    )


@mcp.tool()
async def get_announcements(
    symbol: str, source: str = "nse", market: str = "equities"
) -> dict:
    """Corporate announcements for a stock.

    Args:
        symbol: Exchange ticker in UPPER CASE.
        source: 'nse' (default) or 'sec'.
        market: 'equities' (default) or 'sme'.
    """
    return await get_announcements(symbol, None, source, market)


@mcp.tool()
async def get_shareholdings(
    symbol: str, source: str = "nse"
) -> dict:
    """Shareholding pattern (values in percent).

    Args:
        symbol: Exchange ticker in UPPER CASE.
        source: 'nse' (default) or 'sec'.
    """
    return await get_shareholdings(symbol, None, source)


@mcp.tool()
async def macro_overview(country: str = "in") -> dict:
    """One-shot India macro dashboard: top indices, FII/DII flows, cash turnover, repo rate.

    Args:
        country: Only 'in' is supported.
    """
    return await macro_overview(country)


@mcp.tool()
async def macro_indices(
    country: str = "in", limit: int | None = None
) -> dict:
    """Full NSE index universe with live snapshot, valuation and breadth.

    Args:
        country: Only 'in' is supported.
        limit: Max indices to return.
    """
    return await macro_indices(country, limit)


@mcp.tool()
async def macro_index_quote(country: str, symbol: str) -> dict:
    """Live snapshot for one index (e.g. 'NIFTY 50').

    Args:
        country: Only 'in' is supported.
        symbol: Index symbol, e.g. 'NIFTY 50'.
    """
    return await macro_index_quote(country, symbol)


@mcp.tool()
async def macro_index_history(
    country: str,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Daily OHLC history for an index.

    Args:
        country: Only 'in' is supported.
        symbol: Index symbol, e.g. 'NIFTY 50'.
        start_date: Start date (YYYY-MM-DD). Default: 1 year ago.
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    return await macro_index_history(country, symbol, start_date, end_date)


@mcp.tool()
async def macro_index_valuation(
    country: str,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Historical PE/PB/DY for an index.

    Args:
        country: Only 'in' is supported.
        symbol: Index symbol.
        start_date: Start date (YYYY-MM-DD). Default: 1 year ago.
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    return await macro_index_valuation(country, symbol, start_date, end_date)


@mcp.tool()
async def macro_index_returns(
    country: str,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Total-return (TRI) history for an index.

    Args:
        country: Only 'in' is supported.
        symbol: Index symbol.
        start_date: Start date (YYYY-MM-DD). Default: 1 year ago.
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    return await macro_index_returns(country, symbol, start_date, end_date)


@mcp.tool()
async def macro_index_constituents(
    country: str, symbol: str
) -> dict:
    """Constituent stocks for an index.

    Args:
        country: Only 'in' is supported.
        symbol: Index symbol.
    """
    return await macro_index_constituents(country, symbol)


@mcp.tool()
async def macro_market_valuation(
    country: str, date_str: str | None = None
) -> dict:
    """Whole-market per-equity P/E by trade date.

    Args:
        country: Only 'in' is supported.
        date_str: Trade date (YYYY-MM-DD). Default: latest available.
    """
    return await macro_market_valuation(country, date_str)


@mcp.tool()
async def macro_breadth(country: str, limit: int = 10) -> dict:
    """Advances/declines plus top gainers and losers.

    Args:
        country: Only 'in' is supported.
        limit: Max gainers/losers to return.
    """
    return await macro_breadth(country, limit)


@mcp.tool()
async def macro_flows(country: str) -> dict:
    """FII/DII daily cash-market net flows.

    Args:
        country: Only 'in' is supported.
    """
    return await macro_flows(country)


@mcp.tool()
async def macro_flows_fpi(country: str) -> dict:
    """NSDL FPI flows (requires headless Chromium upstream).

    Args:
        country: Only 'in' is supported.
    """
    return await macro_flows_fpi(country)


@mcp.tool()
async def macro_turnover(country: str) -> dict:
    """Cash-market turnover by segment.

    Args:
        country: Only 'in' is supported.
    """
    return await macro_turnover(country)


@mcp.tool()
async def macro_derivatives(country: str, symbol: str) -> dict:
    """F&O option-chain summary: OI, PCR, max-pain.

    Args:
        country: Only 'in' is supported.
        symbol: Index symbol, e.g. 'NIFTY'.
    """
    return await macro_derivatives(country, symbol)


@mcp.tool()
async def macro_rates(country: str) -> dict:
    """RBI current policy rates and reference FX.

    Args:
        country: Only 'in' is supported.
    """
    return await macro_rates(country)


@mcp.tool()
async def get_news_stories(
    country: str = "in",
    days: int = 7,
    limit: int = 20,
    query: str | None = None,
) -> dict:
    """Latest market news stories (RSS, cached).

    Args:
        country: 'in' (default) or 'us'.
        days: How many days back to fetch.
        limit: Max stories (1-50).
        query: Filter stories by keyword.
    """
    return await get_news_stories(country, days, limit, query)


@mcp.tool()
async def get_ticker_mentions(
    symbol: str, country: str = "in", days: int = 7
) -> dict:
    """News stories mentioning a stock.

    Args:
        symbol: Exchange ticker in UPPER CASE.
        country: 'in' (default) or 'us'.
        days: How many days back to look.
    """
    return await get_ticker_mentions(symbol, country, days)


@mcp.tool()
async def get_reddit(query: str, limit: int = 10) -> dict:
    """Search Reddit for mentions.

    Args:
        query: Search text.
        limit: Max results (1-50).
    """
    return await get_reddit(query, limit)


@mcp.tool()
async def get_youtube_search(query: str, limit: int = 15) -> dict:
    """Search YouTube for videos.

    Args:
        query: Search text.
        limit: Max results (1-50).
    """
    return await get_youtube_search(query, limit)


@mcp.tool()
async def get_youtube_transcript(video_id: str) -> dict:
    """Get YouTube video captions.

    Args:
        video_id: YouTube video ID.
    """
    return await get_youtube_transcript(video_id)


@mcp.tool()
async def build_technical_report(
    symbol: str,
    source: str = "NSE",
    timeframes: str = "daily,weekly,monthly",
) -> dict:
    """End-to-end technical report (~61 sections: trend, indicators, levels).

    Args:
        symbol: Exchange ticker in UPPER CASE.
        source: 'NSE' (default) or 'SEC'.
        timeframes: Comma-separated: 'daily,weekly,monthly'.
    """
    return await build_technical_report(symbol, source, timeframes)


@mcp.tool()
async def read_document(
    url: str, symbol: str | None = None, source: str = "nse"
) -> dict:
    """PageIndex tree + per-page text for a PDF (URL or local path). Parsed once, then cached.
    Returns `text` = [{"page": N, "text": ...}] — slice by node start/end_index.

    Args:
        url: Direct URL to a PDF file, or a local path.
        symbol: Exchange ticker in UPPER CASE.
        source: 'NSE' (default) or 'SEC'.
    """
    return await read_document(url, symbol=symbol, source=source)


@mcp.tool()
async def pull_nse_data(
    symbol: str, filing_type: str | None = None, refresh: bool = False
) -> dict:
    """Pull raw data from NSE into the DB (async job).

    Args:
        symbol: Exchange ticker in UPPER CASE.
        filing_type: 'integrated-filing', 'quarterly-results', 'annual-results', 'shareholding-pattern', or None for all.
        refresh: Re-pull even if data exists.
    """
    return await pull_nse_data(symbol, filing_type, refresh)


@mcp.tool()
async def pull_sec_data(
    symbol: str, filing_type: str | None = None, refresh: bool = False
) -> dict:
    """Pull 10-K/10-Q XBRL from SEC into the DB (async job).

    Args:
        symbol: Exchange ticker in UPPER CASE.
        filing_type: Specific form type, or None for all.
        refresh: Re-pull even if data exists.
    """
    return await pull_sec_data(symbol, filing_type, refresh)


if __name__ == "__main__":
    mcp.run()

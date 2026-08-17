from __future__ import annotations

import asyncio
import functools
from typing import Optional

import typer
from loguru import logger
from rich.text import Text

from src.logging_config import setup_logging

setup_logging()

from __version__ import __version__
from src.cli.render import (
    console,
    render_announcements,
    render_error,
    render_financials,
    render_json,
    render_list,
    render_metrics,
    render_not_implemented,
    render_panel_text,
    render_ping,
    render_pull,
    render_pull_status,
    render_shareholdings,
    render_statements,
)
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core import (
    extract_pdf_content,
    fetch_nse_announcements,
    fetch_nse_annual_reports,
    fetch_nse_financials,
    fetch_nse_shareholdings,
)
from src.db.connection import init_db
from src.db.engine import get_session_factory
from src.db.models import NSEAnnouncement, NSEAnnualReport
from src.models import SOURCE_MODELS
from src.services import (
    ServiceError,
    financial_metrics,
    get_announcements,
    get_financials,
    get_pull_status,
    get_shareholdings,
    get_statement_data,
    list_category,
    pull_nse_data,
)


def _show_help(ctx: typer.Context) -> None:
    """Print a group's help when it is invoked without a subcommand."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


app = typer.Typer(
    help="Voyager — financial data CLI mirroring the Voyager API endpoints.",
    invoke_without_command=True,
    callback=_show_help,
)


def _parse_consolidated(value: str) -> Optional[bool]:
    v = value.strip().lower()
    if v in ("true", "1", "yes", "consolidated"):
        return True
    if v in ("false", "0", "no", "standalone"):
        return False
    if v in ("both", "all", "any", "none"):
        return None
    raise ValueError("--consolidated must be 'true', 'false', or 'both'")


def coro(needs_db: bool = False):
    """Run an async typer command, optionally initialising the DB first."""

    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            async def run_all():
                if needs_db:
                    await init_db()
                return await f(*args, **kwargs)

            try:
                return asyncio.run(run_all())
            except ServiceError as e:
                render_error("Voyager error", e.message)
                raise typer.Exit(code=1)
            except Exception as e:
                logger.exception("Unexpected error")
                render_error("Unexpected error", str(e))
                raise typer.Exit(code=1)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Root commands (mirror API endpoints)
# ---------------------------------------------------------------------------


@app.command()
def ping():
    """Health check (GET /)."""
    render_ping({"ok": 1}, __version__)


@app.command("list")
@coro()
async def list_items(
    category: str = typer.Option(
        "sources",
        help="Category: sources, countries, industries, sectors, indices",
    ),
    country: str = typer.Option("in", help="Country code"),
    source: str = typer.Option("nse", help="Data source"),
):
    """List available categories (GET /list)."""
    render_list(list_category(category, country, source))


@app.command()
def version():
    """Show the Voyager version."""
    line = Text()
    line.append("Voyager", style="bold white")
    line.append(f" v{__version__}", style="cyan")
    console.print(line)


# ---------------------------------------------------------------------------
# financials — GET /financials, /financials/income-statements, ...
# ---------------------------------------------------------------------------

financials_app = typer.Typer(
    help="Financial statements from the DB.",
    invoke_without_command=True,
    callback=_show_help,
)
app.add_typer(financials_app, name="financials")


@financials_app.command("merged")
@coro(needs_db=True)
async def financials_merged(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
    consolidated: bool = typer.Option(
        True, help="Consolidated (True) or standalone (False)"
    ),
    filing_type: str = typer.Option("quarterly", help="quarterly or annual"),
    all_fields: bool = typer.Option(
        False, help="Return all stored fields instead of only priority metrics"
    ),
):
    """Merged income + balance + cash-flow for the latest period (GET /financials)."""
    data = await get_financials(
        symbol, country, source, consolidated, filing_type, all_fields
    )
    render_financials(data)


@financials_app.command("income")
@coro(needs_db=True)
async def financials_income(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
    consolidated: str = typer.Option("true", help="true, false, or both"),
    filing_type: str = typer.Option("quarterly", help="quarterly or annual"),
    limit: int = typer.Option(4, min=0, help="Max periods to show (0 = all)"),
    all_fields: bool = typer.Option(False, help="Return all stored fields"),
):
    """Income statements (GET /financials/income-statements)."""
    data = await get_statement_data(
        "income-statements",
        symbol,
        country,
        source,
        _parse_consolidated(consolidated),
        filing_type,
        limit,
        all_fields,
    )
    render_statements("income-statements", data)


@financials_app.command("balance-sheet")
@coro(needs_db=True)
async def financials_balance_sheet(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
    consolidated: str = typer.Option("true", help="true, false, or both"),
    filing_type: str = typer.Option("quarterly", help="quarterly or annual"),
    limit: int = typer.Option(4, min=0, help="Max periods to show (0 = all)"),
    all_fields: bool = typer.Option(False, help="Return all stored fields"),
):
    """Balance sheets (GET /financials/balance-sheets)."""
    data = await get_statement_data(
        "balance-sheets",
        symbol,
        country,
        source,
        _parse_consolidated(consolidated),
        filing_type,
        limit,
        all_fields,
    )
    render_statements("balance-sheets", data)


@financials_app.command("cash-flow")
@coro(needs_db=True)
async def financials_cash_flow(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
    consolidated: str = typer.Option("true", help="true, false, or both"),
    filing_type: str = typer.Option("quarterly", help="quarterly or annual"),
    limit: int = typer.Option(4, min=0, help="Max periods to show (0 = all)"),
    all_fields: bool = typer.Option(False, help="Return all stored fields"),
):
    """Cash flow statements (GET /financials/cash-flows)."""
    data = await get_statement_data(
        "cash-flows",
        symbol,
        country,
        source,
        _parse_consolidated(consolidated),
        filing_type,
        limit,
        all_fields,
    )
    render_statements("cash-flows", data)


# ---------------------------------------------------------------------------
# pull / pull-status
# ---------------------------------------------------------------------------


@app.command()
@coro(needs_db=True)
async def pull(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
    filing_type: str = typer.Option("quarterly", help="quarterly or annual"),
    refresh: bool = typer.Option(
        False, help="Re-download and re-parse XBRL already in the DB"
    ),
):
    """Pull & parse raw NSE XBRL filings into the DB (POST /pull)."""
    data = await pull_nse_data(symbol, filing_type, refresh)
    render_pull(data)


@app.command("pull-status")
@coro(needs_db=True)
async def pull_status(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Pull history and data availability for a stock (GET /pull)."""
    render_pull_status(await get_pull_status(symbol, country, source))


# ---------------------------------------------------------------------------
# metrics — GET /financial-metrics
# ---------------------------------------------------------------------------


@app.command()
@coro(needs_db=True)
async def metrics(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
    consolidated: bool = typer.Option(
        True, help="Consolidated (True) or standalone (False)"
    ),
    filing_type: str = typer.Option("quarterly", help="quarterly, annual, or ttm"),
):
    """Computed financial metrics for a stock (GET /financial-metrics)."""
    data = await financial_metrics(symbol, country, source, consolidated, filing_type)
    render_metrics(data)


# ---------------------------------------------------------------------------
# announcements / shareholdings
# ---------------------------------------------------------------------------


@app.command()
@coro()
async def announcements(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
    market: str = typer.Option("equities", help="Market segment: equities or sme"),
):
    """Corporate announcements for a stock (GET /announcements)."""
    data = await get_announcements(symbol, country, source, market)
    render_announcements(data)


@app.command()
@coro(needs_db=True)
async def shareholdings(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Shareholding pattern for a stock (GET /shareholdings)."""
    data = await get_shareholdings(symbol, country, source)
    render_shareholdings(data)


# ---------------------------------------------------------------------------
# Not-yet-implemented placeholders
# ---------------------------------------------------------------------------


@app.command()
def funds():
    """Fund data (GET /funds — not yet implemented)."""
    render_not_implemented("funds")


@app.command()
def macro():
    """Macroeconomic data (GET /macro — not yet implemented)."""
    render_not_implemented("macro")


@app.command()
def news():
    """News data (GET /news — not yet implemented)."""
    render_not_implemented("news")


# ---------------------------------------------------------------------------
# tools — legacy NSE / PDF utilities without API counterparts
# ---------------------------------------------------------------------------

tools_app = typer.Typer(
    help="Legacy NSE / PDF utilities.",
    invoke_without_command=True,
    callback=_show_help,
)
app.add_typer(tools_app, name="tools")


@tools_app.command("schema")
def tools_schema(source: str):
    """Show the response model schema for a data source."""
    model = SOURCE_MODELS.get(source.lower())
    if not model:
        render_error("Schema", f"No model found for source '{source}'")
        raise typer.Exit(code=1)
    render_json(model.model_json_schema())


@tools_app.command("nse-financials")
def tools_nse_financials(symbol: str):
    """Fetch and parse raw NSE financial XBRL filings."""
    render_json(fetch_nse_financials(symbol))


@tools_app.command("nse-announcements")
def tools_nse_announcements(
    symbol: str,
    save: bool = typer.Option(False, "--save", help="Save to DB"),
):
    """Fetch raw NSE announcements."""
    results = fetch_nse_announcements(symbol)
    if not save:
        render_json(results)
        return

    async def _save():
        factory = get_session_factory()
        async with factory() as session:
            for x in results:
                ann = NSEAnnouncement(
                    symbol=symbol,
                    an_dt=x.get("an_dt"),
                    attchmnt_text=x.get("attchmntText"),
                    desc=x.get("desc"),
                    attchmnt_file=x.get("attchmntFile"),
                    att_file_size=x.get("attFileSize"),
                    has_xbrl=x.get("hasXbrl", False),
                    sort_date=x.get("sort_date"),
                    raw_data=x,
                )
                session.add(ann)
            await session.commit()

    asyncio.run(_save())
    logger.info("Scrape and save complete")


@tools_app.command("nse-announcements-search")
def tools_nse_announcements_search(
    symbol: str,
    keywords: str = typer.Option("transcript", help="Keyword to match"),
    cutoff_date: str = typer.Option("2026-01-01", help="Max sort_date (YYYY-MM-DD)"),
):
    """Search announcements stored in the DB."""
    import re as re_mod

    async def _search():
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(NSEAnnouncement).where(
                    NSEAnnouncement.symbol == symbol,
                    NSEAnnouncement.attchmnt_text.ilike(f"%{keywords}%"),
                    NSEAnnouncement.sort_date <= cutoff_date,
                )
            )
            return [dict(r._mapping) for r in result]

    docs = asyncio.run(_search())
    render_json(docs)


@tools_app.command("nse-announcements-extract")
def tools_nse_announcements_extract(path_or_url: str):
    """Extract text content of a stored announcement PDF."""
    async def _check():
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(NSEAnnouncement).where(NSEAnnouncement.attchmnt_file == path_or_url)
            )
            return result.scalars().first()

    data = asyncio.run(_check())
    if data is None:
        render_error("Extract", "No document found in DB")
        raise typer.Exit(code=1)
    text = extract_pdf_content(path_or_url)
    render_panel_text("Extracted PDF content", text)


@tools_app.command("nse-list-annual-reports")
def tools_nse_list_annual_reports(symbol: str):
    """List annual reports for a symbol stored in the DB."""
    async def _list():
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(NSEAnnualReport).where(NSEAnnualReport.symbol == symbol)
            )
            return [dict(r._mapping) for r in result]

    render_json(asyncio.run(_list()))


@tools_app.command("nse-annual-reports")
def tools_nse_annual_reports(
    symbol: str,
    save: bool = typer.Option(False, "--save", help="Save to DB"),
):
    """Fetch annual report metadata from NSE."""
    results = fetch_nse_annual_reports(symbol)
    if not save:
        render_json(results)
        return

    async def _save():
        factory = get_session_factory()
        async with factory() as session:
            for x in results:
                report = NSEAnnualReport(
                    symbol=symbol,
                    file_name=x.get("fileName"),
                    raw_data=x,
                )
                session.add(report)
            await session.commit()

    asyncio.run(_save())
    logger.info("Scrape and save complete")


@tools_app.command("nse-shareholdings")
@tools_app.command("nse-full-download")
def tools_nse_full_download(symbol: str):
    """Run the full legacy NSE scrape (financials, announcements, shareholdings, annual reports)."""
    tools_nse_financials(symbol)
    tools_nse_announcements(symbol)
    tools_nse_shareholdings(symbol)
    tools_nse_annual_reports(symbol)
    logger.info("Full data scrape complete")


if __name__ == "__main__":
    app()

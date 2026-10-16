"""Ingest mode for cli.py — runs the src.ingest pipeline interactively.

Separate module so the pure API-explorer path never pays the pandas/edgar
import cost. One event loop per session: init_db, every ingest call, and
engine disposal all run on it (asyncpg connections are loop-bound).
"""

import asyncio
import contextlib
from pathlib import Path

from cli_ui import BACK, QUIT, console, pick_index, select

from src.db import engine as db_engine
from src.db.engine import init_db
from src.ingest import (
    download_and_store,
    ingest_document,
    pull_announcements,
    pull_macro_breadth,
    pull_macro_derivatives,
    pull_macro_flows,
    pull_macro_indices,
    pull_macro_index_constituents,
    pull_macro_index_history,
    pull_macro_index_quote,
    pull_macro_index_returns,
    pull_macro_index_valuation,
    pull_macro_market_valuation,
    pull_macro_overview,
    pull_macro_rates,
    pull_macro_turnover,
    pull_nse_data,
    pull_sec_data,
    refresh_news,
    refresh_shareholdings,
)
from src.logging_config import setup_logging


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [dim]({default})[/dim]" if default else ""
    return console.input(f"[bold]{prompt}[/bold]{suffix}: ").strip()


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _line(label: str, note: str) -> None:
    console.print(f"  [cyan]{label:<16}[/cyan] {note}")


def _is_skip(result: dict) -> bool:
    return result.get("status") == "skipped" or (
        result.get("saved") == 0 and bool(result.get("skipped"))
    )


_STATEMENT_BREAKDOWN = (
    ("parsed_income_statements", "inc"),
    ("parsed_balance_sheets", "bal"),
    ("parsed_cash_flows", "cf"),
    ("parsed_shareholdings", "sh"),
)


def _breakdown(result: dict) -> str:
    """One-line split of a pull: total records + per-statement counts.

    `records_pulled` already equals the raw-filing total, so the raw endpoint
    names (announcements/quarterly-results/...) are dropped to keep it on one
    line. Errors are appended when a pull was partial.
    """
    bd = result.get("endpoint_breakdown") or {}
    statements = [(label, bd[key]) for key, label in _STATEMENT_BREAKDOWN if bd.get(key)]
    parts = [f"{result.get('records_pulled', 0)} records"]
    if statements:
        parts.append(" ".join(f"{n} {label}" for label, n in statements))
    errors = {k: v for k, v in bd.items() if isinstance(v, str) and v != "no data"}
    if errors:
        parts.append("; ".join(f"{k}: {v}" for k, v in errors.items()))
    return " · ".join(parts)


def _describe(result: dict) -> str:
    status = result.get("status")
    if status == "no data":
        return "[yellow]no data[/yellow]"
    if status == "skipped":
        return f"[yellow]skipped[/yellow] [dim]({result.get('reason', 'already stored')})[/dim]"
    if status in ("completed", "partial"):
        return f"[green]{_breakdown(result)}[/green]"
    if "saved" in result:
        if _is_skip(result):
            return f"[yellow]skipped[/yellow] [dim]({result['skipped']})[/dim]"
        return f"[green]{result['saved']} saved[/green]"
    if "rows" in result:
        span = ""
        if result.get("start_date"):
            span = f" [dim]({result['start_date']}→{result['end_date']})[/dim]"
        return f"[green]{result['rows']} bars[/green]{span}"
    if "stored" in result:
        return "[green]stored[/green]" if result["stored"] else "[yellow]fresh — skipped[/yellow]"
    if "num_pages" in result:
        cached = " [dim]cached[/dim]" if result.get("cached") else ""
        return f"[green]{result['num_pages']} pages[/green]{cached}"
    return str(result)


def parse_index(path) -> list[tuple[str, str, str]]:
    """Index rows are `SYMBOL [EXCHANGE] [COUNTRY]`. Returns upper-cased tuples."""
    rows = []
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        rows.append(
            (
                parts[0].upper(),
                parts[1].upper() if len(parts) > 1 else "",
                parts[2].upper() if len(parts) > 2 else "IN",
            )
        )
    return rows


def _scope():
    """Ask single-symbol vs bulk index. Returns (kind, path) or None if backed out."""
    choice = select(
        "Scope",
        [
            ("single", "Single symbol", "one ticker"),
            ("batch", "Index file", "every symbol in a saved list"),
        ],
        subtitle="Download for one symbol, or in bulk.",
    )
    if choice in (BACK, QUIT):
        return None
    if choice == "batch":
        path = pick_index()
        if path in (BACK, QUIT):
            return None
        return ("batch", path)
    return ("single", None)


def _menu(title, options, subtitle=""):
    """Numbered menu. Returns the chosen value, or None if backed out."""
    choice = select(title, options, subtitle=subtitle)
    return None if choice in (BACK, QUIT) else choice


def _symbol() -> str:
    """Pick a ticker: type it, or choose one from an index list. '' = cancelled."""
    choice = select(
        "Symbol",
        [
            ("type", "Type it", "enter a ticker manually"),
            ("index", "From an index list", "pick a saved symbol"),
        ],
        subtitle="Which symbol to pull?",
    )
    if choice in (BACK, QUIT):
        return ""
    if choice == "index":
        path = pick_index()
        if path in (BACK, QUIT):
            return ""
        rows = parse_index(path)
        picked = select(path.stem, [(s, s, f"{e} · {c}") for s, e, c in rows])
        return "" if picked in (BACK, QUIT) else picked
    return _ask("Symbol (e.g. VBL, AAPL)").upper()


async def _single(symbol: str, fn, *args) -> None:
    try:
        with console.status(f"[cyan]{symbol}[/cyan]"):
            result = await fn(symbol, *args)
    except Exception as exc:
        _line(symbol, f"[red]{exc}[/red]")
        return
    _line(symbol, _describe(result))


async def _run_batch(rows, make_call) -> None:
    total = len(rows)
    ok = skipped = failed = 0
    agg: dict[str, int] = {}
    # ponytail: sequential on purpose — NSE/SEC are rate-limited, so concurrency
    # mostly buys 429s. Add bounded asyncio.gather if the APIs ever allow it.
    for i, (symbol, exchange, country) in enumerate(rows, 1):
        try:
            with console.status(f"[cyan]{i}/{total} {symbol}[/cyan]"):
                result = await make_call(symbol, exchange, country)
        except Exception as exc:
            failed += 1
            _line(f"{i}/{total} {symbol}", f"[red]{exc}[/red]")
            continue
        if _is_skip(result):
            skipped += 1
        else:
            ok += 1
        _line(f"{i}/{total} {symbol}", _describe(result))
        for key, val in (result.get("endpoint_breakdown") or {}).items():
            if isinstance(val, int):
                agg[key] = agg.get(key, 0) + val
    summary = (
        f"[bold]Done[/bold] — [green]{ok} ok[/green], "
        f"[yellow]{skipped} skipped[/yellow], [red]{failed} failed[/red]"
    )
    if agg:
        pulled = sum(v for k, v in agg.items() if not k.startswith("parsed_"))
        summary += " · " + _breakdown({"records_pulled": pulled, "endpoint_breakdown": agg})
    console.print(f"\n{summary}")


async def _batch(path, make_call) -> None:
    rows = parse_index(path)
    console.print()
    console.print(f"[dim]{len(rows)} symbols from {Path(path).name} — this can take a while[/dim]")
    await _run_batch(rows, make_call)


async def _financials() -> None:
    scope = _scope()
    if scope is None:
        return
    kind, path = scope
    filing_type = _menu(
        "Filing type",
        [
            ("quarterly", "Quarterly", "3-month periods"),
            ("annual", "Annual", "full-year periods"),
        ],
        subtitle="NSE/SEC reporting frequency",
    )
    if filing_type is None:
        return
    refresh = _menu(
        "Refresh stored data?",
        [
            (False, "No", "skip symbols already in the DB"),
            (True, "Yes", "re-download and overwrite"),
        ],
    )
    if refresh is None:
        return
    if kind == "single":
        symbol = _symbol()
        if not symbol:
            return
        source = _menu(
            "Source",
            [("nse", "NSE", "India"), ("sec", "SEC", "US EDGAR")],
            subtitle="Where the filings live",
        )
        if source is None:
            return
        fn = pull_sec_data if source == "sec" else pull_nse_data
        await _single(symbol, fn, filing_type, refresh)
    else:
        await _batch(
            path,
            lambda s, e, c: pull_sec_data(s, filing_type, refresh)
            if c == "US"
            else pull_nse_data(s, filing_type, refresh),
        )


async def _prices() -> None:
    scope = _scope()
    if scope is None:
        return
    kind, path = scope
    period = _menu("Period", [(p, p, "") for p in ("3mo", "6mo", "1y", "2y", "5y", "10y", "max")])
    if period is None:
        return
    interval = _menu("Interval", [(i, i, "") for i in ("1d", "1wk", "1mo")])
    if interval is None:
        return
    if kind == "single":
        symbol = _symbol()
        if not symbol:
            return
        exchange = _menu("Exchange", [(e, e, "") for e in ("NSE", "BSE", "NYSE", "NASDAQ")])
        if exchange is None:
            return
        country = _menu("Country", [("IN", "India", ""), ("US", "United States", "")])
        if country is None:
            return
        await _single(symbol, download_and_store, exchange, country, period, interval)
    else:
        # Index rows carry exchange + country, so no extra prompts are needed.
        await _batch(path, lambda s, e, c: download_and_store(s, e, c, period, interval))


async def _news() -> None:
    country = _menu("Country", [("in", "India", ""), ("us", "United States", "")])
    if country is None:
        return
    days = _int(_ask("Lookback days", "7"), 7)
    try:
        with console.status("[cyan]fetching headlines[/cyan]"):
            stored = await refresh_news(country, days)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        return
    _line(country.upper(), "[green]stored[/green]" if stored else "[yellow]fresh — skipped[/yellow]")


async def _shareholdings() -> None:
    scope = _scope()
    if scope is None:
        return
    kind, path = scope
    if kind == "single":
        symbol = _symbol()
        if not symbol:
            return
        await _single(symbol, refresh_shareholdings)
    else:
        # NSE shareholding pattern is India-only; drop US rows from the list.
        rows = [row for row in parse_index(path) if row[2] != "US"]
        console.print()
        console.print(f"[dim]{len(rows)} NSE symbols from {Path(path).name}[/dim]")
        await _run_batch(rows, lambda s, e, c: refresh_shareholdings(s))


async def _documents() -> None:
    url = _ask("PDF URL or local path")
    if not url:
        return
    symbol = _ask("Symbol (optional)").upper()
    try:
        with console.status("[cyan]indexing[/cyan]"):
            result = await ingest_document(url, symbol=symbol or None)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        return
    _line(symbol or Path(url).name[:16], _describe(result))


async def _announcements() -> None:
    scope = _scope()
    if scope is None:
        return
    kind, path = scope
    if kind == "single":
        symbol = _symbol()
        if not symbol:
            return
        await _single(symbol, pull_announcements)
    else:
        rows = parse_index(path)
        console.print()
        console.print(f"[dim]{len(rows)} symbols from {Path(path).name}[/dim]")
        await _run_batch(rows, lambda s, e, c: pull_announcements(s))


async def _macro() -> None:
    choice = select(
        "Macro",
        [
            ("overview", "Overview", "top indices + flows + rates"),
            ("indices", "All indices", "full NSE index universe"),
            ("breadth", "Breadth", "advances/declines + gainers/losers"),
            ("flows", "FII/DII flows", "daily cash-market flows"),
            ("turnover", "Turnover", "cash turnover by segment"),
            ("rates", "RBI rates", "policy rates + FX"),
            ("derivatives", "Derivatives", "option chain — pick an index"),
            ("index_history", "Index history", "daily OHLC — pick an index"),
            ("index_valuation", "Index valuation", "PE/PB/DY — pick an index"),
            ("index_returns", "Index returns", "total return — pick an index"),
            ("index_constituents", "Constituents", "member stocks — pick an index"),
            ("market_valuation", "Market P/E", "whole-market PE snapshot"),
        ],
        subtitle="Macro data — NSE/RBI upstream",
        multiline=True,
    )
    if choice in (BACK, QUIT):
        return
    try:
        with console.status(f"[cyan]pulling {choice}[/cyan]"):
            if choice == "overview":
                r = await pull_macro_overview()
            elif choice == "indices":
                r = await pull_macro_indices()
            elif choice == "breadth":
                r = await pull_macro_breadth()
            elif choice == "flows":
                r = await pull_macro_flows()
            elif choice == "turnover":
                r = await pull_macro_turnover()
            elif choice == "rates":
                r = await pull_macro_rates()
            elif choice == "market_valuation":
                r = await pull_macro_market_valuation()
            elif choice == "derivatives":
                r = await pull_macro_derivatives(_ask("Index symbol (e.g. NIFTY)").upper())
            elif choice == "index_history":
                r = await pull_macro_index_history(_ask("Index symbol").upper())
            elif choice == "index_valuation":
                r = await pull_macro_index_valuation(_ask("Index symbol").upper())
            elif choice == "index_returns":
                r = await pull_macro_index_returns(_ask("Index symbol").upper())
            elif choice == "index_constituents":
                r = await pull_macro_index_constituents(_ask("Index symbol").upper())
            else:
                return
        _line(choice, _describe(r))
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")


_HANDLERS = {
    "financials": _financials,
    "prices": _prices,
    "news": _news,
    "shareholdings": _shareholdings,
    "announcements": _announcements,
    "documents": _documents,
    "macro": _macro,
}


async def _ingest_loop() -> None:
    while True:
        choice = select(
            "Ingest",
            [
                ("financials", "Financials", "XBRL statements — NSE / SEC"),
                ("prices", "Prices", "OHLCV bars"),
                ("news", "News", "RSS headlines by country"),
                ("shareholdings", "Shareholdings", "NSE shareholding pattern"),
                ("announcements", "Announcements", "NSE announcements — equities + SME"),
                ("documents", "Documents", "index a PDF by URL or path"),
                ("macro", "Macro", "NSE/RBI macro data — indices, flows, rates"),
            ],
            subtitle="Pull data into Postgres — pick an item, then single-symbol or bulk",
            multiline=True,
        )
        if choice in (BACK, QUIT):
            return
        try:
            await _HANDLERS[choice]()
        except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
            return
        except Exception as exc:
            console.print(f"\n[red]Ingest failed:[/red] {exc}")


def ingest_mode() -> None:
    try:
        asyncio.run(_ingest_session())
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        console.print("\n[dim]Goodbye![/dim]")


async def _ingest_session() -> None:
    # Keep per-item INFO logs in the file sink; only WARNING+ reaches the
    # terminal so the menus and progress output stay readable.
    setup_logging(console=False)
    await init_db()
    try:
        await _ingest_loop()
    finally:
        # A SIGINT delivered during a sync input() leaves the task's
        # pending-cancel flag set; that flag would land on dispose()'s first
        # await and abort the asyncpg close (the "Exception closing
        # connection" traceback). sleep(0) forces one suspension so the
        # cancel is spent here instead. ceiling: one Ctrl+C handled — a
        # second one during dispose still aborts it; upgrade to a
        # non-cancellable dispose task if that ever matters.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0)
        if db_engine.engine is not None:
            with contextlib.suppress(Exception):
                await db_engine.engine.dispose()

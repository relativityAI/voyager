"""Voyager remote client — talks to a deployed Voyager API over HTTPS.

Configuration (env vars or root flags):
    VOYAGER_BASE_URL   e.g. https://voyager-api.onrender.com
    VOYAGER_API_KEY    the service API key (data:read)

Example:
    python -m client --base-url https://voyager-api.onrender.com metrics VBL
"""

import json
import time
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from client.api import VoyagerClient, VoyagerError
from client.config import Config, load_config

console = Console()

app = typer.Typer(
    help="Voyager API client — query a deployed Voyager instance.",
    no_args_is_help=True,
    add_completion=False,
    invoke_without_command=True,
)

_shared: dict = {}


@app.callback()
def _root(
    ctx: typer.Context,
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        envvar="VOYAGER_BASE_URL",
        help="Voyager API base URL, e.g. https://voyager-api.onrender.com",
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", envvar="VOYAGER_API_KEY", help="Service API key (data:read)."
    ),
):
    _shared["config"] = load_config(base_url, api_key)


def _config() -> Config:
    config = _shared.get("config") or load_config()
    if not config.base_url:
        console.print(
            "[red]Missing base URL. Set VOYAGER_BASE_URL or pass --base-url.[/red]"
        )
        raise typer.Exit(code=1)
    return config


def _client() -> VoyagerClient:
    return VoyagerClient(_config())


def _print(data, title: Optional[str] = None) -> None:
    if title:
        console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print(json.dumps(data, indent=2, default=str))


def _table(columns, rows) -> None:
    table = Table(show_header=True, header_style="bold cyan")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(c) if c is not None else "" for c in row])
    console.print(table)


def _fatal(exc: VoyagerError) -> None:
    console.print(f"[red]{exc}[/red]")
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Read commands
# ---------------------------------------------------------------------------


@app.command()
def ping():
    """Health check."""
    try:
        _print(_client().get("/"))
    except VoyagerError as exc:
        _fatal(exc)


@app.command()
def metrics(
    symbol: str,
    filing_type: str = typer.Option("quarterly", help="quarterly, annual, or ttm"),
    consolidated: bool = typer.Option(True),
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Computed financial metrics for a stock (GET /financial-metrics)."""
    params = {
        "symbol": symbol,
        "filing_type": filing_type,
        "consolidated": str(consolidated).lower(),
        "country": country,
        "source": source,
    }
    try:
        _print(_client().get("/financial-metrics", params=params), f"Metrics: {symbol}")
    except VoyagerError as exc:
        _fatal(exc)


@app.command()
def financials(
    symbol: str,
    filing_type: str = typer.Option("quarterly"),
    consolidated: bool = typer.Option(True),
    all_fields: bool = typer.Option(False, "--all-fields"),
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Merged income + balance + cash-flow for the latest period (GET /financials)."""
    params = {
        "symbol": symbol,
        "filing_type": filing_type,
        "consolidated": str(consolidated).lower(),
        "all_fields": str(all_fields).lower(),
        "country": country,
        "source": source,
    }
    try:
        _print(_client().get("/financials", params=params), f"Financials: {symbol}")
    except VoyagerError as exc:
        _fatal(exc)


@app.command()
def statements(
    symbol: str,
    statement: str = typer.Argument(help="income, balance, or cash-flow"),
    limit: int = typer.Option(4),
    filing_type: str = typer.Option("quarterly"),
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Raw statement rows (GET /financials/{income-statements|balance-sheets|cash-flows})."""
    route_map = {
        "income": "income-statements",
        "balance": "balance-sheets",
        "cash-flow": "cash-flows",
    }
    route = route_map.get(statement)
    if route is None:
        console.print(f"[red]statement must be one of: {', '.join(route_map)}[/red]")
        raise typer.Exit(code=1)
    params = {
        "symbol": symbol,
        "filing_type": filing_type,
        "limit": limit,
        "country": country,
        "source": source,
    }
    try:
        _print(
            _client().get(f"/financials/{route}", params=params),
            f"{statement}: {symbol}",
        )
    except VoyagerError as exc:
        _fatal(exc)


@app.command()
def announcements(
    symbol: str,
    market: str = typer.Option("equities"),
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Corporate announcements (GET /announcements)."""
    try:
        data = _client().get(
            "/announcements",
            params={
                "symbol": symbol,
                "market": market,
                "country": country,
                "source": source,
            },
        )
        _print(data, f"Announcements: {symbol}")
    except VoyagerError as exc:
        _fatal(exc)


@app.command()
def shareholdings(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Shareholding pattern (GET /shareholdings)."""
    try:
        _print(
            _client().get(
                "/shareholdings",
                params={"symbol": symbol, "country": country, "source": source},
            )
        )
    except VoyagerError as exc:
        _fatal(exc)


@app.command()
def technicals(
    symbol: str,
    timeframes: str = typer.Option(
        "daily,weekly,monthly",
        help="Comma-separated: intraday, daily, weekly, monthly",
    ),
    source: str = typer.Option("nse"),
    out: Optional[str] = typer.Option(
        None, "--out", help="Write the full JSON report to this file"
    ),
):
    """End-to-end technical analysis report (GET /technicals)."""
    try:
        data = _client().get(
            "/technicals",
            params={"symbol": symbol, "timeframes": timeframes, "source": source},
        )
    except VoyagerError as exc:
        _fatal(exc)
        return

    sections = data.get("sections", {})
    ok = sum(1 for s in sections.values() if s.get("status") == "ok")
    console.print(
        f"[bold cyan]Technical report: {symbol}[/bold cyan]  "
        f"{ok}/{len(sections)} sections supported"
    )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("section")
    table.add_column("status")
    for name, sec in sections.items():
        status = sec.get("status", "?")
        style = "green" if status == "ok" else "yellow"
        table.add_row(name, f"[{style}]{status}[/{style}]")
    console.print(table)

    if out:
        with open(out, "w") as f:
            json.dump(data, f, indent=2, default=str)
        console.print(f"[green]Full report written to {out}[/green]")


@app.command()
def list_categories(
    category: str = typer.Option("sources"),
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """List available categories (GET /list)."""
    try:
        _print(
            _client().get(
                "/search-categories",
                params={"category": category, "country": country, "source": source},
            )
        )
    except VoyagerError as exc:
        _fatal(exc)


if __name__ == "__main__":
    app()

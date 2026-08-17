"""Voyager remote client — talks to a deployed Voyager API over HTTPS.

Configuration (env vars or root flags):
    VOYAGER_BASE_URL   e.g. https://voyager-api.onrender.com
    VOYAGER_API_KEY    the service API key (data:read)
    VOYAGER_ADMIN_KEY  the admin key (only for `keys` commands)

Example:
    python -m client --base-url https://voyager-api.onrender.com metrics VBL
    python -m client keys create --name "main-app" --scopes data:read
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
keys_app = typer.Typer(
    help="Manage API keys (requires VOYAGER_ADMIN_KEY).", no_args_is_help=True
)
app.add_typer(keys_app, name="keys")

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
    admin_key: Optional[str] = typer.Option(
        None,
        "--admin-key",
        envvar="VOYAGER_ADMIN_KEY",
        help="Admin key (keys commands).",
    ),
):
    _shared["config"] = load_config(base_url, api_key, admin_key)


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


def _job_status_style(status: str) -> str:
    """Color a job status: done green, queued/running yellow, else red."""
    if status in ("done", "completed"):
        return f"[green]{status}[/green]"
    if status in ("queued", "running"):
        return f"[yellow]{status}[/yellow]"
    return f"[red]{status}[/red]"


def _endpoint_style(value) -> str:
    """Color an endpoint breakdown cell: counts green, 'no data' dim, else red."""
    if isinstance(value, int):
        return f"[green]{value}[/green]"
    text = str(value)
    if text.lower() in ("no data", "no_data"):
        return f"[dim]{text}[/dim]"
    if len(text) > 80:
        text = text[:77] + "..."
    return f"[red]{text}[/red]"


def _render_pull_job(job) -> None:
    """Render a single pull job: summary, result status, endpoint breakdown."""
    console.print(
        f"[bold cyan]Pull job {job.get('job_id')}[/bold cyan]  "
        f"{job.get('symbol')}  {_job_status_style(job.get('status', 'unknown'))}"
    )
    _table(
        ["field", "value"],
        [
            ("symbol", job.get("symbol")),
            ("filing_type", job.get("filing_type")),
            ("refresh", str(job.get("refresh", False)).lower()),
            ("created_at", job.get("created_at")),
            ("started_at", job.get("started_at")),
            ("finished_at", job.get("finished_at")),
        ],
    )
    if job.get("error"):
        console.print(f"[red]error: {job['error']}[/red]")
    result = job.get("result")
    if not result:
        return
    console.print(
        f"[bold cyan]Result:[/bold cyan] "
        f"status={_job_status_style(result.get('status'))}  "
        f"records={result.get('records_pulled')}  "
        f"xbrl_parsed={result.get('xbrl_parsed')}"
    )
    breakdown = result.get("endpoint_breakdown") or {}
    if breakdown:
        _table(
            ["endpoint", "result"],
            [(k, _endpoint_style(v)) for k, v in breakdown.items()],
        )
    timing = result.get("timing") or {}
    total = timing.get("total_ms")
    phases = timing.get("phases") or {}
    if total is not None:
        phases = {**phases, "total": total}
    if phases:
        _table(["phase", "ms"], [(k, str(v)) for k, v in phases.items()])


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
def list_categories(
    category: str = typer.Option("sources"),
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """List available categories (GET /list)."""
    try:
        _print(
            _client().get(
                "/list",
                params={"category": category, "country": country, "source": source},
            )
        )
    except VoyagerError as exc:
        _fatal(exc)


@app.command()
def pull_status(
    symbol: str,
    country: str = typer.Option("in"),
    source: str = typer.Option("nse"),
):
    """Pull history and data availability (GET /pull)."""
    try:
        _print(
            _client().get(
                "/pull", params={"symbol": symbol, "country": country, "source": source}
            )
        )
    except VoyagerError as exc:
        _fatal(exc)


# ---------------------------------------------------------------------------
# Write commands (need data:write scope)
# ---------------------------------------------------------------------------


@app.command()
def pull(
    symbol: str,
    filing_type: str = typer.Option("quarterly"),
    refresh: bool = typer.Option(False, "--refresh"),
    watch: bool = typer.Option(True, "--watch/--no-watch"),
):
    """Submit an async pull job (POST /pull, needs data:write scope)."""
    try:
        params = {
            "symbol": symbol,
            "filing_type": filing_type,
            "refresh": str(refresh).lower(),
        }
        submitted = _client().post("/pull", params=params)
    except VoyagerError as exc:
        _fatal(exc)

    job_id = submitted.get("job_id")
    console.print(
        f"[green]Submitted pull for {symbol}[/green]  job_id={job_id} status={submitted.get('status')}"
    )
    if not watch or not job_id:
        return

    for _ in range(120):
        time.sleep(2)
        try:
            job = _client().get(f"/pull/jobs/{job_id}")
        except VoyagerError as exc:
            _fatal(exc)
        if job["status"] in ("done", "failed"):
            _print(job, f"Pull job {job_id}")
            if job["status"] == "failed":
                raise typer.Exit(code=1)
            return
    console.print(
        "[yellow]Timed out waiting for job to finish; check `pull-jobs`.[/yellow]"
    )
    raise typer.Exit(code=1)


@app.command()
def pull_jobs(
    limit: int = typer.Option(20),
):
    """List recent pull jobs (needs data:write scope)."""
    try:
        jobs = _client().get("/pull/jobs", params={"limit": limit})
    except VoyagerError as exc:
        _fatal(exc)
    rows = [
        (
            j["job_id"],
            j["symbol"],
            j["filing_type"],
            j["status"],
            j.get("finished_at") or j["created_at"],
        )
        for j in jobs
    ]
    _table(["job_id", "symbol", "filing_type", "status", "time"], rows)


@app.command()
def pull_job(
    job_id: str,
):
    """Show one pull job's status, result, and endpoint breakdown."""
    try:
        job = _client().get(f"/pull/jobs/{job_id}")
    except VoyagerError as exc:
        _fatal(exc)
    _render_pull_job(job)


# ---------------------------------------------------------------------------
# Admin: API key management
# ---------------------------------------------------------------------------


@keys_app.command()
def create(
    name: str,
    owner: str = typer.Option(""),
    scopes: str = typer.Option("data:read", help="Comma-separated scopes"),
    rpm: int = typer.Option(60),
    expires_in_days: Optional[int] = typer.Option(None, "--expires-in-days"),
):
    """Create an API key. Shows the raw key once."""
    body = {
        "name": name,
        "owner": owner,
        "scopes": [s.strip() for s in scopes.split(",") if s.strip()],
        "rpm": rpm,
        "expires_in_days": expires_in_days,
    }
    try:
        result = _client().post("/admin/keys", json=body, admin=True)
    except VoyagerError as exc:
        _fatal(exc)
    console.print(
        "[bold green]API key created. Store it now — it is shown only once:[/bold green]"
    )
    console.print(f"[bold]{result['key']}[/bold]")
    _print(result["api_key"], "Key details")


@keys_app.command()
def list_keys():
    """List API keys (raw keys/hashes never shown)."""
    try:
        keys = _client().get("/admin/keys", admin=True)
    except VoyagerError as exc:
        _fatal(exc)
    rows = [
        (
            k["prefix"],
            k["name"],
            k["owner"],
            ",".join(k["scopes"]),
            k["rpm"],
            "yes" if k["enabled"] else "no",
        )
        for k in keys
    ]
    _table(["prefix", "name", "owner", "scopes", "rpm", "enabled"], rows)


@keys_app.command()
def revoke(
    prefix: str,
):
    """Revoke an API key by prefix."""
    try:
        _print(_client().delete(f"/admin/keys/{prefix}", admin=True))
    except VoyagerError as exc:
        _fatal(exc)


@keys_app.command()
def enable(
    prefix: str,
):
    """Re-enable a revoked API key."""
    try:
        _print(_client().post(f"/admin/keys/{prefix}/enable", admin=True))
    except VoyagerError as exc:
        _fatal(exc)


if __name__ == "__main__":
    app()

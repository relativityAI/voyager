#!/usr/bin/env python3
"""Interactive CLI for the Voyager API.

Reads API key from .env, fetches OpenAPI spec, lets you pick endpoints
interactively, fills in args, and renders results with rich.

Usage:
    python cli.py                          # interactive mode
    python cli.py --url http://localhost:8001
    python cli.py --download RELIANCE NSE IN
    python cli.py --download --file stocks.txt
"""

import argparse
import json
import os
import readline
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.table import Table

console = Console()

DEFAULT_API_URL = "http://localhost:8001"

ASCII_LOGO = r"""
[bold cyan]
 ╦  ╦╔═╗╦ ╦╔═╗╔═╗╔═╗╦═╗
 ╚╗╔╝║ ║╚╦╝║ ╦║ ╦║╣ ╠╦╝
  ╚╝ ╚═╝╩ ╚═╝╚═╝╚═╝╚═╝╩╚═
[/bold cyan]
[dim]  Financial Data API — Interactive CLI[/dim]
"""


def load_api_key() -> str:
    load_dotenv()
    key = os.getenv("VOYAGER_API_KEY", "") or os.getenv("API_KEY", "")
    if not key:
        console.print("[red]No API key found. Set VOYAGER_API_KEY in .env[/red]")
        sys.exit(1)
    return key


def fetch_openapi(base_url: str) -> dict:
    try:
        resp = requests.get(f"{base_url}/openapi.json", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        console.print(f"[red]Failed to fetch OpenAPI from {base_url}: {exc}[/red]")
        sys.exit(1)


def flatten_endpoints(spec: dict) -> list[dict]:
    endpoints = []
    for path, methods in spec.get("paths", {}).items():
        for method, detail in methods.items():
            if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                endpoints.append({
                    "method": method.upper(),
                    "path": path,
                    "summary": detail.get("summary", ""),
                    "description": detail.get("description", ""),
                    "parameters": detail.get("parameters", []),
                    "tags": detail.get("tags", []),
                })
    return endpoints


def pick_endpoint(endpoints: list[dict]) -> dict:
    tag_groups: dict[str, list[tuple[int, dict]]] = {}
    for i, ep in enumerate(endpoints):
        tag = ep["tags"][0] if ep["tags"] else "Other"
        tag_groups.setdefault(tag, []).append((i, ep))

    while True:
        console.print("\n[bold]Endpoint groups:[/bold]\n")
        tags = sorted(tag_groups.keys())
        for i, tag in enumerate(tags, 1):
            count = len(tag_groups[tag])
            console.print(f"  [cyan]{i}[/cyan]. {tag} [dim]({count} endpoints)[/dim]")
        console.print(f"  [cyan]a[/cyan]. [bold]All endpoints[/bold] [dim]({len(endpoints)})[/dim]")
        console.print(f"  [cyan]s[/cyan]. [bold]Search[/bold]")
        console.print(f"  [cyan]d[/cyan]. [bold]Download prices[/bold] (bulk, from file or typed)")
        console.print(f"  [red]q[/red]. Quit")

        choice = console.input("\n[bold]Select group[/bold]: ").strip().lower()

        if choice == "q":
            sys.exit(0)
        if choice == "d":
            return "DOWNLOAD_PRICES"
        if choice == "s":
            query = console.input("[bold]Search[/bold]: ").strip().lower()
            if query:
                filtered = [(i, ep) for i, ep in enumerate(endpoints)
                           if query in ep["path"].lower() or query in ep["summary"].lower()]
                if filtered:
                    return _pick_from_list(filtered)
                console.print("[yellow]No matches found[/yellow]")
            continue
        if choice == "a":
            return _pick_from_list(list(enumerate(endpoints)))

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(tags):
                return _pick_from_list(tag_groups[tags[idx]])
        except ValueError:
            pass
        console.print("[red]Invalid selection[/red]")


def _pick_from_list(items: list[tuple[int, dict]]) -> dict:
    console.print()
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Method", width=8)
    table.add_column("Path", style="green")
    table.add_column("Summary", style="white")

    for display_num, (original_idx, ep) in enumerate(items, 1):
        method_color = {
            "GET": "blue", "POST": "green", "PUT": "yellow",
            "DELETE": "red", "PATCH": "magenta",
        }.get(ep["method"], "white")
        table.add_row(
            str(display_num),
            f"[{method_color}]{ep['method']}[/{method_color}]",
            ep["path"],
            ep["summary"][:60],
        )

    console.print(table)

    while True:
        try:
            choice = console.input("\n[bold]Select endpoint #[/bold] ([red]b[/red] back, [red]q[/red] quit): ")
            if choice.lower() == "b":
                return None
            if choice.lower() == "q":
                console.print("[dim]Goodbye![/dim]")
                sys.exit(0)
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx][1]
            console.print("[red]Invalid selection[/red]")
        except ValueError:
            console.print("[red]Invalid input[/red]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            sys.exit(0)


def collect_params(endpoint: dict) -> dict:
    params = {}

    console.print(f"\n[bold]{endpoint['method']} {endpoint['path']}[/bold]")
    if endpoint["summary"]:
        console.print(f"[dim]{endpoint['summary']}[/dim]")
    console.print()

    for param in endpoint.get("parameters", []):
        name = param.get("name", "")
        if name.lower() in ("x-api-key", "authorization", "x-voyager-admin-key"):
            continue
        required = param.get("required", False)
        schema = param.get("schema", {})
        param_type = schema.get("type", "string")
        default = schema.get("default", None)
        description = param.get("description", "")
        enum = schema.get("enum", None)

        is_path_param = f"{{{name}}}" in endpoint["path"]

        req_marker = "[red]*[/red]" if required else "[dim](optional)[/dim]"
        desc = f" — {description[:80]}" if description else ""
        default_str = f" [dim](default: {default})[/dim]" if default is not None else ""

        if enum:
            console.print(f"  [bold]{name}[/bold] {req_marker}{default_str}{desc}")
            for j, val in enumerate(enum, 1):
                console.print(f"    [cyan]{j}[/cyan]. {val}")
            while True:
                choice = console.input(f"  Select [dim](Enter for {'default: ' + str(default) if default else 'none'})[/dim]: ")
                if not choice and default is not None:
                    params[name] = default
                    break
                if not choice and not required:
                    break
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(enum):
                        params[name] = enum[idx]
                        break
                except ValueError:
                    pass
                console.print("  [red]Invalid choice[/red]")
        else:
            prompt = f"  [bold]{name}[/bold] {req_marker}{default_str}{desc}: "
            val = console.input(prompt)
            if not val and default is not None:
                val = str(default)
            if not val and required and is_path_param:
                console.print(f"  [red]{name} is required[/red]")
                continue
            if val:
                if param_type == "integer":
                    try:
                        params[name] = int(val)
                    except ValueError:
                        params[name] = val
                elif param_type == "boolean":
                    params[name] = val.lower() in ("true", "1", "yes", "y")
                elif "symbol" in name.lower() or "ticker" in name.lower():
                    params[name] = val.upper()
                else:
                    params[name] = val

    return params


def call_api(base_url: str, api_key: str, endpoint: dict, params: dict) -> dict:
    url = f"{base_url}{endpoint['path']}"
    headers = {"X-API-Key": api_key}

    path_params = {}
    query_params = {}
    for key, val in params.items():
        if f"{{{key}}}" in endpoint["path"]:
            path_params[key] = val
        else:
            query_params[key] = val

    for key, val in path_params.items():
        url = url.replace(f"{{{key}}}", str(val))

    try:
        if endpoint["method"] == "GET":
            resp = requests.get(url, headers=headers, params=query_params, timeout=30)
        elif endpoint["method"] == "POST":
            resp = requests.post(url, headers=headers, params=query_params, timeout=30)
        elif endpoint["method"] == "DELETE":
            resp = requests.delete(url, headers=headers, params=query_params, timeout=30)
        else:
            resp = requests.request(endpoint["method"], url, headers=headers, params=query_params, timeout=30)

        if resp.status_code == 200:
            return {"ok": True, "data": resp.json()}
        else:
            try:
                err = resp.json()
            except Exception:
                err = {"detail": resp.text}
            return {"ok": False, "status": resp.status_code, "error": err}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": {"detail": str(exc)}}


def download_via_api(api_url: str, api_key: str, symbol: str, exchange: str, country: str,
                     period: str = "max", interval: str = "1d") -> dict:
    endpoint = {"method": "POST", "path": "/download-prices"}
    params = {
        "symbol": symbol,
        "exchange": exchange,
        "country": country,
        "period": period,
        "interval": interval,
    }
    return call_api(api_url, api_key, endpoint, params)


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    n /= 1024
    if n < 1024:
        return f"{n:.1f} KB"
    n /= 1024
    if n < 1024:
        return f"{n:.1f} MB"
    return f"{n / 1024:.1f} GB"


def _human_period(start: str, end: str) -> str:
    from datetime import date
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    days = (e - s).days
    years = days // 365
    months = (days % 365) // 30
    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}m")
    return " ".join(parts) if parts else f"{days}d"


def render_response(result: dict):
    if not result["ok"]:
        err = result.get("error", {})
        if isinstance(err, dict):
            detail = err.get("detail", str(err))
            code = err.get("code", "")
            msg = f"{code}: {detail}" if code else str(detail)
        else:
            msg = str(err)
        console.print(f"\n[red]Error {result['status']}:[/red] {msg}")
        return

    data = result["data"]
    size = len(json.dumps(data).encode())

    if isinstance(data, dict) and "rows" in data and "symbol" in data:
        if data["rows"] == -1:
            console.print(f"\n[yellow]Skipped:[/yellow] {data['symbol']}.{data['exchange']}.{data['country']} — data already present")
            return
        period = ""
        if data.get("start_date") and data.get("end_date"):
            period = f" ({_human_period(data['start_date'], data['end_date'])})"
        console.print(f"\n[green]Done:[/green] {data['symbol']}.{data['exchange']}.{data['country']} — {data['rows']:,} rows{period} — {_human_size(size)}")
        return

    console.print(f"[dim]Response size: {_human_size(size)}[/dim]\n")

    if isinstance(data, list):
        if not data:
            console.print("[dim]Empty result[/dim]")
            return
        if isinstance(data[0], dict):
            render_table(data)
        else:
            for item in data:
                console.print(f"  • {item}")
    elif isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            meta_keys = [k for k in data if k != "data"]
            for k in meta_keys:
                console.print(f"[bold cyan]{k}:[/bold cyan] {data[k]}")
            console.print()
            render_table(data["data"])
        elif "data" in data and isinstance(data["data"], dict):
            for k, v in data.items():
                if k == "data":
                    console.print()
                    render_dict_pretty(v)
                else:
                    console.print(f"[bold cyan]{k}:[/bold cyan] {v}")
        else:
            render_dict_pretty(data)
    else:
        console.print(str(data))


def render_table(items: list[dict]):
    if not items:
        return
    all_keys = []
    seen = set()
    for item in items:
        for k in item:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    priority = ["date", "symbol", "exchange", "close", "open", "high", "low", "volume",
                "period", "interval", "total", "limit", "offset", "name", "value"]
    ordered = [k for k in priority if k in all_keys]
    ordered += [k for k in all_keys if k not in ordered]

    max_cols = 8
    if len(ordered) > max_cols:
        ordered = ordered[:max_cols]

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False)
    for key in ordered:
        table.add_column(key, overflow="fold", max_width=30)

    for item in items[:100]:
        row = []
        for key in ordered:
            val = item.get(key, "")
            if isinstance(val, float):
                val = f"{val:.2f}"
            row.append(str(val) if val is not None else "")
        table.add_row(*row)

    console.print(table)
    if len(items) > 100:
        console.print(f"[dim]... and {len(items) - 100} more rows[/dim]")


def render_dict_pretty(data: dict, indent: int = 0):
    for key, val in data.items():
        if isinstance(val, dict):
            console.print(f"{'  ' * indent}[bold cyan]{key}:[/bold cyan]")
            render_dict_pretty(val, indent + 1)
        elif isinstance(val, list):
            console.print(f"{'  ' * indent}[bold cyan]{key}:[/bold cyan] [{len(val)} items]")
            if val and not isinstance(val[0], (dict, list)):
                preview = ", ".join(str(v) for v in val[:5])
                console.print(f"{'  ' * (indent + 1)}[dim]{preview}{'...' if len(val) > 5 else ''}[/dim]")
            elif val:
                for item in val[:3]:
                    if isinstance(item, dict):
                        render_dict_pretty(item, indent + 1)
                    else:
                        console.print(f"{'  ' * (indent + 1)}- {item}")
                if len(val) > 3:
                    console.print(f"{'  ' * (indent + 1)}[dim]... +{len(val) - 3} more[/dim]")
        else:
            console.print(f"{'  ' * indent}[bold cyan]{key}:[/bold cyan] {val}")


def _interactive_download(api_url: str, api_key: str):
    console.print("\n[bold]Download[/bold]")
    console.print("  1. Prices (OHLCV)")
    console.print("  2. Financials")
    mode = console.input("\nSelect: ").strip()

    console.print("\n  1. From file")
    console.print("  2. Type symbols")
    choice = console.input("\nSelect: ").strip()

    stocks = []
    if choice == "1":
        file_path = console.input("File path: ").strip()
        p = Path(file_path)
        if not p.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            return
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                stocks.append((parts[0].upper(), parts[1].upper(), parts[2].upper() if len(parts) > 2 else "IN"))
    elif choice == "2":
        symbols_input = console.input("Symbols (comma-separated, e.g. AAPL,MSFT,RELIANCE): ").strip()
        exchange = console.input("Exchange (e.g. NASDAQ, NSE): ").strip().upper() or "NASDAQ"
        country = console.input("Country (e.g. US, IN): ").strip().upper() or "US"
        for sym in symbols_input.split(","):
            sym = sym.strip().upper()
            if sym:
                stocks.append((sym, exchange, country))
    else:
        console.print("[red]Invalid choice[/red]")
        return

    if not stocks:
        console.print("[red]No stocks to download[/red]")
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    if mode == "2":
        filing_type = console.input("Filing type (quarterly/annual) \\[quarterly]: ").strip() or "quarterly"
        workers = console.input("Concurrent workers \\[4]: ").strip() or "4"
        console.print(f"\n[bold]Downloading financials for {len(stocks)} stock(s)[/bold]  workers={workers}\n")
        endpoint = {"method": "POST", "path": "/download-financials"}

        def _fetch(sym, ex, co):
            source = "SEC" if co == "US" else "nse"
            result = call_api(api_url, api_key, endpoint, {
                "symbol": sym, "source": source, "filing_type": filing_type, "refresh": "false",
            })
            return sym, result

        ok = fail = 0
        with ThreadPoolExecutor(max_workers=int(workers)) as pool:
            futures = {pool.submit(_fetch, s, e, c): s for s, e, c in stocks}
            for future in as_completed(futures):
                sym, result = future.result()
                render_response(result)
                if result["ok"]:
                    ok += 1
                else:
                    fail += 1
        console.print(f"\n[bold]Summary:[/bold] {ok} downloaded, {fail} failed")
        return

    period = console.input("Period (3mo/6mo/1y/2y/5y/10y/max) \\[max]: ").strip() or "max"
    interval = console.input("Interval (1d/1wk/1mo) \\[1d]: ").strip() or "1d"
    workers = console.input("Concurrent workers \\[4]: ").strip() or "4"

    console.print(f"\n[bold]Downloading {len(stocks)} stock(s)[/bold]  period={period}  interval={interval}  workers={workers}\n")

    def _fetch_price(sym, ex, co):
        result = download_via_api(api_url, api_key, sym, ex, co, period, interval)
        return sym, result

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=int(workers)) as pool:
        futures = {pool.submit(_fetch_price, s, e, c): s for s, e, c in stocks}
        for future in as_completed(futures):
            sym, result = future.result()
            render_response(result)
            if result["ok"]:
                ok += 1
            else:
                fail += 1

    console.print(f"\n[bold]Summary:[/bold] {ok} downloaded, {fail} failed")


def interactive_mode(api_url: str, api_key: str):
    spec = fetch_openapi(api_url)
    endpoints = flatten_endpoints(spec)

    while True:
        try:
            console.clear()
            console.print(ASCII_LOGO)
            endpoint = pick_endpoint(endpoints)
            if endpoint is None:
                continue
            if endpoint == "DOWNLOAD_PRICES":
                _interactive_download(api_url, api_key)
                console.input("[dim]Press Enter to continue...[dim]")
                continue
            params = collect_params(endpoint)
            console.print(f"\n[dim]Calling {endpoint['method']} {endpoint['path']}...[/dim]")
            result = call_api(api_url, api_key, endpoint, params)
            console.print()
            render_response(result)
            console.print()
            console.input("[dim]Press Enter to continue...[dim]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break


def _parse_stocks_from_file(file) -> list[tuple[str, str, str]]:
    stocks = []
    for line in file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            stocks.append((parts[0].upper(), parts[1].upper(), parts[2].upper() if len(parts) > 2 else "IN"))
    return stocks


def download_mode(args, api_url: str, api_key: str):
    stocks = []
    if args.file:
        stocks = _parse_stocks_from_file(args.file)
    else:
        for sym in args.symbols:
            stocks.append((sym.upper(), args.exchange or "NSE", args.country or "IN"))

    if getattr(args, "download_financials", False):
        console.print(f"\n[bold]Downloading financials for {len(stocks)} stock(s)[/bold]\n")
        endpoint = {"method": "POST", "path": "/download-financials"}
        ok = fail = 0
        for symbol, exchange, country in stocks:
            source = "SEC" if country == "US" else "nse"
            result = call_api(api_url, api_key, endpoint, {
                "symbol": symbol, "source": source,
                "filing_type": getattr(args, "filing_type", "quarterly"),
                "refresh": "false",
            })
            render_response(result)
            if result["ok"]:
                ok += 1
            else:
                fail += 1
        console.print(f"\n[bold]Summary:[/bold] {ok} downloaded, {fail} failed")
        return

    console.print(f"\n[bold]Downloading OHLCV for {len(stocks)} stock(s)[/bold]")
    console.print(f"  Period: [cyan]{args.period}[/cyan]  Interval: [cyan]{args.interval}[/cyan]\n")

    ok = fail = 0
    for symbol, exchange, country in stocks:
        result = download_via_api(api_url, api_key, symbol, exchange, country, args.period, args.interval)
        render_response(result)
        if result["ok"]:
            ok += 1
        else:
            fail += 1

    console.print(f"\n[bold]Summary:[/bold] {ok} downloaded, {fail} failed")


def main():
    parser = argparse.ArgumentParser(description="Interactive Voyager API CLI")
    parser.add_argument("--url", "-u", default=DEFAULT_API_URL, help="API base URL")
    parser.add_argument("--download", "-d", action="store_true", help="Download mode (not interactive)")
    parser.add_argument("--download-financials", action="store_true", help="Bulk download financials instead of prices")
    parser.add_argument("--filing-type", default="quarterly", choices=["quarterly", "annual"])
    parser.add_argument("--file", "-f", type=Path, help="File with SYMBOL EXCHANGE COUNTRY per line")
    parser.add_argument("--period", "-p", default="max", choices=["3mo", "6mo", "1y", "2y", "5y", "10y", "max"])
    parser.add_argument("--interval", "-i", default="1d", choices=["1d", "1wk", "1mo"])
    parser.add_argument("--exchange", "-e", default="NSE")
    parser.add_argument("--country", "-c", default="IN")
    parser.add_argument("symbols", nargs="*", help="Symbol(s) to download")
    args = parser.parse_args()

    api_key = load_api_key()
    if args.download:
        download_mode(args, args.url, api_key)
    else:
        interactive_mode(args.url, api_key)


if __name__ == "__main__":
    main()

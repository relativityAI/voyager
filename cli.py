#!/usr/bin/env python3
"""Interactive CLI for the Voyager API.

Home menu splits into two sections: the API explorer (reads the OpenAPI spec,
lets you pick endpoints, fills in args, renders results with rich) and Ingest
(writes the data-ingestion pipeline against the same Postgres the API reads).

Usage:
    python cli.py                          # home menu: API or Ingest
    python cli.py --api                     # straight to the API explorer
    python cli.py --ingest                  # straight to the ingest pipeline
    python cli.py --url http://localhost:8001

The API is read-only; ingestion runs through this CLI's Ingest mode.
"""

import argparse
import json
import os

import requests
from dotenv import load_dotenv
from rich import box
from rich.table import Table

from cli_ui import BACK, QUIT, console, header, select

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
        console.print("[yellow]No API key set (VOYAGER_API_KEY) — API calls will fail; ingest still works.[/yellow]")
    return key


def fetch_openapi(base_url: str):
    try:
        resp = requests.get(f"{base_url}/openapi.json", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        console.print(f"[yellow]API unreachable at {base_url} ({exc}) — returning to menu.[/yellow]")
        return None


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


def pick_endpoint(endpoints: list[dict]):
    tag_groups: dict[str, list[tuple[int, dict]]] = {}
    for i, ep in enumerate(endpoints):
        tag = ep["tags"][0] if ep["tags"] else "Other"
        tag_groups.setdefault(tag, []).append((i, ep))

    while True:
        header("Endpoint groups")
        tags = sorted(tag_groups.keys())
        for i, tag in enumerate(tags, 1):
            count = len(tag_groups[tag])
            console.print(f"  [cyan]{i}[/cyan]. {tag} [dim]({count} endpoints)[/dim]")
        console.print(f"  [cyan]a[/cyan]. [bold]All endpoints[/bold] [dim]({len(endpoints)})[/dim]")
        console.print(f"  [cyan]s[/cyan]. [bold]Search[/bold]")
        console.print("  [red]q[/red]. Quit")

        choice = console.input("\n[bold]Select group[/bold]: ").strip().lower()

        if choice == "q":
            return QUIT
        if choice == "s":
            query = console.input("[bold]Search[/bold] [dim](b to cancel)[/dim]: ").strip().lower()
            if query == "b":
                continue
            if query:
                filtered = [(i, ep) for i, ep in enumerate(endpoints)
                            if query in ep["path"].lower() or query in ep["summary"].lower()]
                if filtered:
                    picked = _pick_from_list(filtered)
                    if picked in (BACK, QUIT):
                        return picked
                    if picked is not None:
                        return picked
                console.print("[yellow]No matches found[/yellow]")
            continue
        if choice == "a":
            picked = _pick_from_list(list(enumerate(endpoints)))
            if picked in (BACK, QUIT):
                return picked
            if picked is not None:
                return picked

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(tags):
                picked = _pick_from_list(tag_groups[tags[idx]])
                if picked in (BACK, QUIT):
                    return picked
                if picked is not None:
                    return picked
        except ValueError:
            pass
        console.print("[red]Invalid selection[/red]")


def _pick_from_list(items: list[tuple[int, dict]]):
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
                return QUIT
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx][1]
            console.print("[red]Invalid selection[/red]")
        except ValueError:
            console.print("[red]Invalid input[/red]")
        except (KeyboardInterrupt, EOFError):
            return QUIT


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
            if val.strip().lower() == "b" and not params:
                return None
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


def api_section(api_url: str, api_key: str):
    spec = fetch_openapi(api_url)
    if spec is None:
        return
    endpoints = flatten_endpoints(spec)

    while True:
        try:
            console.clear()
            console.print(ASCII_LOGO)
            endpoint = pick_endpoint(endpoints)
            if endpoint in (BACK, QUIT):
                return
            if endpoint is None:
                continue
            params = collect_params(endpoint)
            if params is None:
                continue
            console.print(f"\n[dim]Calling {endpoint['method']} {endpoint['path']}...[/dim]")
            result = call_api(api_url, api_key, endpoint, params)
            console.print()
            render_response(result)
            console.print()
            console.input("[dim]Press Enter to continue...[dim]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            return


def home(api_url: str, api_key: str):
    while True:
        console.clear()
        console.print(ASCII_LOGO)
        choice = select(
            "Voyager",
            [
                ("api", "API", "explore the read API"),
                ("ingest", "Ingest", "pull data into Postgres"),
            ],
            subtitle="Financial data — read API and ingestion",
            multiline=True,
        )
        if choice in (BACK, QUIT):
            console.print("[dim]Goodbye![/dim]")
            return
        if choice == "api":
            api_section(api_url, api_key)
        else:
            from ingest_cli import ingest_mode

            ingest_mode()


def main():
    parser = argparse.ArgumentParser(description="Interactive Voyager CLI (API explorer + local ingest)")
    parser.add_argument("--url", "-u", default=DEFAULT_API_URL, help="API base URL")
    parser.add_argument("--ingest", "-i", action="store_true", help="Go straight to the local ingest pipeline")
    parser.add_argument("--api", "-a", action="store_true", help="Go straight to the API explorer")
    args = parser.parse_args()

    if args.ingest:
        from ingest_cli import ingest_mode

        ingest_mode()
        return

    api_key = load_api_key()
    try:
        if args.api:
            api_section(args.url, api_key)
            return
        home(args.url, api_key)
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C / Ctrl+D at any prompt — exit cleanly instead of a traceback.
        console.print("\n[dim]Goodbye![/dim]")


if __name__ == "__main__":
    main()

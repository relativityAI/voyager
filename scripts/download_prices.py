#!/usr/bin/env python3
"""Download OHLCV price data from yfinance and store in DB.

Usage:
    python scripts/download_prices.py RELIANCE NSE IN
    python scripts/download_prices.py AAPL NASDAQ US
    python scripts/download_prices.py --file stocks.txt
    python scripts/download_prices.py --file stocks.txt --period 5y --interval 1d

File format (one per line): SYMBOL EXCHANGE COUNTRY
    RELIANCE NSE IN
    AAPL NASDAQ US
    MSFT NASDAQ US
"""

import argparse
import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

console = Console()

_DEFAULT_EXCHANGES = {
    "IN": "NSE",
    "US": "NASDAQ",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Download OHLCV price data from yfinance")
    parser.add_argument("symbols", nargs="*", help="Symbol(s) to download")
    parser.add_argument("--file", "-f", type=Path, help="File with SYMBOL EXCHANGE COUNTRY per line")
    parser.add_argument("--period", "-p", default="max", choices=["3mo", "6mo", "1y", "2y", "5y", "10y", "max"])
    parser.add_argument("--interval", "-i", default="1d", choices=["1d", "1wk", "1mo"])
    parser.add_argument("--exchange", "-e", help="Exchange (if single symbol, e.g. NSE, NASDAQ)")
    parser.add_argument("--country", "-c", help="Country code (if single symbol, e.g. IN, US)")
    return parser.parse_args()


def load_stocks(args) -> list[tuple[str, str, str]]:
    stocks = []

    if args.file:
        if not args.file.exists():
            console.print(f"[red]File not found: {args.file}[/red]")
            sys.exit(1)
        for line in args.file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                console.print(f"[yellow]Skipping invalid line: {line}[/yellow]")
                continue
            symbol = parts[0].upper()
            exchange = parts[1].upper()
            country = parts[2].upper() if len(parts) > 2 else ("IN" if exchange in ("NSE", "BSE") else "US")
            stocks.append((symbol, exchange, country))
    elif args.symbols:
        for sym in args.symbols:
            symbol = sym.upper()
            exchange = (args.exchange or _DEFAULT_EXCHANGES.get(args.country, "NSE")).upper()
            country = (args.country or ("IN" if exchange in ("NSE", "BSE") else "US")).upper()
            stocks.append((symbol, exchange, country))
    else:
        console.print("[red]Provide symbols or --file[/red]")
        sys.exit(1)

    return stocks


def main():
    args = parse_args()
    stocks = load_stocks(args)

    if not stocks:
        console.print("[red]No stocks to download[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Downloading OHLCV for {len(stocks)} stock(s)[/bold]")
    console.print(f"  Period: [cyan]{args.period}[/cyan]  Interval: [cyan]{args.interval}[/cyan]\n")

    from src.tools.prices import download_ohlcv

    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Downloading...", total=len(stocks))

        for symbol, exchange, country in stocks:
            progress.update(task, description=f"Downloading {symbol}.{exchange}...")
            try:
                rows = download_ohlcv(symbol, exchange, country, args.period, args.interval)
                results.append((symbol, exchange, country, rows, None))
            except Exception as exc:
                results.append((symbol, exchange, country, 0, str(exc)))
            progress.advance(task)

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    table.add_column("Symbol", style="cyan")
    table.add_column("Exchange", style="magenta")
    table.add_column("Country", style="blue")
    table.add_column("Rows", justify="right", style="green")
    table.add_column("Status")

    for symbol, exchange, country, rows, error in results:
        if error:
            table.add_row(symbol, exchange, country, "0", f"[red]FAIL: {error}[/red]")
        else:
            table.add_row(symbol, exchange, country, str(rows), "[green]OK[/green]")

    console.print("\n")
    console.print(table)

    ok = sum(1 for _, _, _, _, e in results if e is None)
    fail = len(results) - ok
    console.print(f"\n[bold]Done:[/bold] {ok} succeeded, {fail} failed")


if __name__ == "__main__":
    main()

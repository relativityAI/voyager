"""Shared UI for the two interactive CLI sections (API explorer + ingest).

Deliberately free of src.ingest imports so the API path never pays the pandas
cost. Every menu renders through rich and reads through a single `console`, so
tests patch one seam (`cli_ui.console.input`).
"""

import readline  # noqa: F401  — input history for the interactive prompts
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

console = Console()

DATA_DIR = Path(__file__).parent / "data"
INDICES_DIR = DATA_DIR / "indices"

# Returned by select() when a navigation shortcut is used instead of an option.
BACK = "__back__"
QUIT = "__quit__"

# Option counts at or below this render as a single inline prompt.
_INLINE_MAX = 10


def header(title: str, subtitle: str = "") -> None:
    console.print()
    console.print(Rule(f"[bold]{title}[/bold]", style="cyan"))
    if subtitle:
        console.print(f"[dim]{subtitle}[/dim]")
    console.print()


def select(title, options, subtitle="", *, back=True, quit=True, multiline=False):
    """Choose an option by number.

    Small sets render as a single inline prompt (``Filing type (1) Quarterly
    (2) Annual b back q quit ›``); large sets — e.g. the 500-row symbol list —
    render as a numbered list. Pass ``multiline=True`` for the top-level menus
    (home, ingest) to keep the fuller box layout. Returns the chosen value, or
    BACK / QUIT.
    """
    while True:
        if not multiline and len(options) <= _INLINE_MAX:
            parts = [f"[bold]{title}[/bold]"]
            if subtitle:
                parts.append(f"[dim]{subtitle}[/dim]")
            for i, (_, label, _) in enumerate(options, 1):
                parts.append(f"[cyan]({i})[/cyan] {label}")
            if back:
                parts.append("[dim]b back[/dim]")
            if quit:
                parts.append("[dim]q quit[/dim]")
            choice = console.input("  ".join(parts) + " [bold]›[/bold] ").strip().lower()
        else:
            header(title, subtitle)
            for i, (_, label, hint) in enumerate(options, 1):
                tail = f"  [dim]{hint}[/dim]" if hint else ""
                console.print(f"  [cyan]{i}[/cyan]. {label}{tail}")
            shortcuts = []
            if back:
                shortcuts.append("[cyan]b[/cyan] back")
            if quit:
                shortcuts.append("[red]q[/red] quit")
            if shortcuts:
                console.print("  " + "  ".join(shortcuts))
            choice = console.input("\n[bold]Select[/bold] [dim]›[/dim] ").strip().lower()
        if back and choice == "b":
            return BACK
        if quit and choice == "q":
            return QUIT
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        console.print("[red]Invalid selection[/red]")


def pick_index():
    """Choose a symbol list from data/indices. Returns Path, or BACK."""
    files = sorted(INDICES_DIR.glob("*.txt"))
    if not files:
        console.print(f"[yellow]No index files in {INDICES_DIR}[/yellow]")
        return BACK
    options = []
    for path in files:
        count = sum(1 for line in path.read_text().splitlines() if line.strip())
        options.append((path, path.stem, f"{count} symbols"))
    return select("Index file", options, subtitle="Bulk source of symbols to pull")

import json
from typing import Any, Dict, List, Optional

from rich import box as rich_box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.services._common import _load_priority_metrics

from .styles import (
    ACCENT,
    ERR,
    HEADER,
    LABEL,
    METRIC_GROUPS,
    METRIC_LABELS,
    MUTED,
    NEG,
    POS,
    VALUE,
    WARN,
    _to_float,
    fmt_amount,
    fmt_metric_value,
    humanize_key,
)

console = Console()
error_console = Console(stderr=True)

_META_KEYS = {
    "symbol",
    "consolidated",
    "filing_type",
    "period_end_date",
    "period_start_date",
    "xbrl_url",
    "broadcast_date",
    "measure",
    "entity_identifier",
    "fiscal_period",
    "source_endpoint",
    "context_ref_type",
    "pulled_at",
}

_STATEMENT_TITLES = {
    "income-statements": "Income Statements",
    "balance-sheets": "Balance Sheets",
    "cash-flows": "Cash Flows",
}

_SHAREHOLDING_LABELS = {
    "promoters_and_promoter_group": "Promoters & Promoter Group",
    "foreign_institutional_investors": "Foreign Institutional Investors (FII)",
    "domestic_institutional_investors": "Domestic Institutional Investors (DII)",
    "non_institutions": "Non-Institutions",
    "public_shareholding": "Public Shareholding",
    "non_promoter_non_public_shareholding": "Non-Promoter Non-Public",
}


_INNER_BOX = rich_box.SIMPLE_HEAD


def _kv_table(rows: List, title: Optional[str] = None) -> Table:
    table = Table(show_header=False, box=None, pad_edge=False)
    if title:
        table.title = f"[bold white]{title}[/]"
    table.add_column(style=LABEL, no_wrap=True)
    table.add_column(style=VALUE)
    for label, value in rows:
        table.add_row(label, value)
    return table


def _panel(title: str, renderable: Any) -> Panel:
    return Panel(
        renderable,
        title=f"[bold white]{title}[/]",
        border_style=MUTED,
        padding=(0, 1),
    )


def _fmt_statement_cell(key: str, v: Any) -> Text:
    f = _to_float(v)
    if f is None:
        return Text("—", style=LABEL)
    if key == "debt_equity_ratio":
        s = f"{f:,.2f}"
    else:
        s = fmt_amount(f)
    return Text(s, style=NEG if f < 0 else VALUE)


# ---------------------------------------------------------------------------
# Root commands
# ---------------------------------------------------------------------------


def render_ping(data: Dict[str, Any], version: str) -> None:
    ok = data.get("ok")
    line = Text()
    line.append("Voyager", style=HEADER)
    line.append(f" v{version}", style=ACCENT)
    line.append("   status: ", style=LABEL)
    line.append("ok" if ok else "unavailable", style=POS if ok else ERR)
    console.print(Panel(line, border_style=MUTED))


def render_list(data: Dict[str, Any]) -> None:
    rows = data.get("data") or []
    meta = Text(
        f"category: {data['category']}   country: {data['country']}   "
        f"source: {data['source']}",
        style=LABEL,
    )
    if not rows:
        console.print(
            Panel(
                Text("No data for this category.", style=LABEL),
                title=f"[bold white]{data['category']}[/]",
                border_style=MUTED,
            )
        )
        console.print(meta)
        return

    cols = list(rows[0].keys())
    table = Table(
        title=f"[bold white]{data['category']}[/]",
        header_style=HEADER,
        border_style=MUTED,
        row_styles=["", "dim"],
    )
    for c in cols:
        table.add_column(humanize_key(c))
    for r in rows:
        table.add_row(*[str(r.get(c, "")) for c in cols])
    console.print(table)
    console.print(meta)


# ---------------------------------------------------------------------------
# Financials
# ---------------------------------------------------------------------------


def _classify_financial_key(key: str, priority: Dict[str, Any]) -> Optional[str]:
    if key in _META_KEYS:
        return None
    for section, name in (
        ("income_statements", "Income Statement"),
        ("balance_sheets", "Balance Sheet"),
        ("cash_flows", "Cash Flow"),
    ):
        if key in priority.get(section, set()):
            return name
    return "Other"


def render_financials(data: Dict[str, Any]) -> None:
    priority = _load_priority_metrics()
    symbol = data.get("symbol", "")
    period = data.get("period_end_date")

    header = Text()
    header.append(str(symbol), style=HEADER)
    header.append("  merged financials", style=LABEL)
    header.append(f"   · period: {period or '—'}", style=LABEL)
    if data.get("filing_type"):
        header.append(f" · {data['filing_type']}", style=LABEL)
    console.print(header)

    sections: Dict[str, List] = {}
    for key, value in data.items():
        name = _classify_financial_key(key, priority)
        if name is None:
            continue
        sections.setdefault(name, []).append((key, value))

    for name in (
        "Income Statement",
        "Balance Sheet",
        "Cash Flow",
        "Other",
    ):
        rows = sections.get(name)
        if not rows:
            continue
        kv_rows = []
        for key, value in rows:
            if key in ("debt_equity_ratio",):
                display = Text(
                    f"{_to_float(value):,.2f}" if _to_float(value) is not None else "—",
                    style=VALUE,
                )
            else:
                display = _fmt_statement_cell(key, value)
            kv_rows.append((humanize_key(key), display))
        console.print(_panel(name, _kv_table(kv_rows)))


def render_statements(statement: str, data: Dict[str, Any]) -> None:
    records: List[Dict[str, Any]] = next(iter(data.values()), [])
    title = _STATEMENT_TITLES.get(statement, humanize_key(statement))

    if not records:
        console.print(
            Panel(
                Text(f"No {title.lower()} found.", style=LABEL),
                title=f"[bold white]{title}[/]",
                border_style=MUTED,
            )
        )
        return

    records = sorted(
        records, key=lambda r: str(r.get("period_end_date", "")), reverse=True
    )

    first = records[0]
    meta_parts = []
    if first.get("symbol"):
        meta_parts.append(str(first["symbol"]))
    if first.get("consolidated") is not None:
        meta_parts.append("consolidated" if first["consolidated"] else "standalone")
    if first.get("filing_type"):
        meta_parts.append(str(first["filing_type"]))
    if first.get("source_endpoint"):
        meta_parts.append(str(first["source_endpoint"]))
    if first.get("period_start_date"):
        meta_parts.append(f"from {first['period_start_date']}")
    console.print(_build_statement_header(meta_parts))

    metric_keys: List[str] = []
    for rec in records:
        for k in rec:
            if k not in _META_KEYS and k not in metric_keys:
                metric_keys.append(k)

    table = Table(header_style=HEADER, border_style=MUTED, row_styles=["", "dim"])
    table.add_column("Metric", style=LABEL, no_wrap=True)
    for rec in records:
        table.add_column(str(rec.get("period_end_date", "—")), justify="right")
    for key in metric_keys:
        table.add_row(
            humanize_key(key),
            *[_fmt_statement_cell(key, rec.get(key)) for rec in records],
        )
    console.print(table)


def _build_statement_header(meta_parts: List[str]) -> Text:
    if not meta_parts:
        return Text("", style=LABEL)
    text = Text()
    for i, part in enumerate(meta_parts):
        if i > 0:
            text.append("  ·  ", style=LABEL)
        text.append(part, style=HEADER if i == 0 else LABEL)
    return text


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

_STATUS_STYLE = {"completed": POS, "partial": WARN, "failed": ERR}


def render_pull(data: Dict[str, Any]) -> None:
    status = data.get("status", "unknown")
    header = Text()
    header.append("PULL  ", style=LABEL)
    header.append(str(data.get("symbol", "")), style=HEADER)
    header.append("   status: ", style=LABEL)
    header.append(status, style=_STATUS_STYLE.get(status, VALUE))
    console.print(Panel(header, border_style=MUTED))

    summary = _kv_table(
        [
            ("Records pulled", Text(str(data.get("records_pulled", 0)), style=VALUE)),
            ("XBRL parsed", Text(str(data.get("xbrl_parsed", 0)), style=VALUE)),
        ]
    )
    console.print(summary)

    breakdown = data.get("endpoint_breakdown", {})
    if breakdown:
        table = Table(header_style=HEADER, border_style=MUTED, box=_INNER_BOX)
        table.add_column("Endpoint", style=LABEL)
        table.add_column("Result", justify="right")
        for ep, result in breakdown.items():
            if isinstance(result, int):
                display = Text(f"{result}", style=POS if result > 0 else VALUE)
            elif result == "no data":
                display = Text(result, style=LABEL)
            else:
                display = Text(str(result), style=ERR)
            table.add_row(ep, display)
        console.print(_panel("Endpoint Breakdown", table))

    timing = data.get("timing", {})
    parts = []
    phases = timing.get("phases", {})
    for name in ("setup", "fetch", "existing_scan", "dedup", "xbrl", "db"):
        val = phases.get(name)
        if val is not None and val > 0:
            parts.append(f"{name}={val:.0f}ms")
    if timing.get("total_ms") is not None:
        parts.append(f"total={timing['total_ms']:.0f}ms")
    if parts:
        console.print(Text("  ".join(parts), style=LABEL))

    counts = timing.get("counts", {})
    skipped = {k: v for k, v in counts.items() if v and k.startswith("skipped_")}
    if skipped:
        console.print(
            Text(
                "  ".join(
                    f"{k.replace('skipped_', '')}={v}" for k, v in skipped.items()
                ),
                style=LABEL,
            )
        )


def render_pull_status(data: Dict[str, Any]) -> None:
    header = Text()
    header.append(str(data.get("symbol", "")), style=HEADER)
    header.append(f"   source: {data.get('source', 'NSE')}", style=LABEL)
    if data.get("last_pull"):
        header.append(f"   last pull: {data['last_pull']:%Y-%m-%d %H:%M}", style=LABEL)
    console.print(Panel(header, border_style=MUTED))

    available = bool(data.get("available"))
    summary = _kv_table(
        [
            (
                "Available",
                Text("yes" if available else "no", style=POS if available else WARN),
            ),
            ("Total records", Text(str(data.get("total_records", 0)), style=VALUE)),
            ("Total pulls", Text(str(data.get("total_pulls", 0)), style=VALUE)),
            (
                "Previous pulls",
                Text(str(data.get("previous_pulls_count", 0)), style=LABEL),
            ),
        ]
    )
    console.print(summary)

    counts = data.get("record_counts", {})
    if counts:
        table = Table(header_style=HEADER, border_style=MUTED, box=_INNER_BOX)
        table.add_column("Collection", style=LABEL)
        table.add_column("Records", justify="right")
        for label, count in counts.items():
            if isinstance(count, int):
                table.add_row(label, Text(f"{count}", style=VALUE))
            else:
                table.add_row(label, Text(str(count), style=ERR))
        console.print(_panel("Record Counts", table))

    breakdown = data.get("financial_breakdown", {})
    if breakdown:
        table = Table(header_style=HEADER, border_style=MUTED, box=_INNER_BOX)
        table.add_column("Collection", style=LABEL)
        table.add_column("Type", style=LABEL)
        table.add_column("Records", justify="right")
        table.add_column("Periods", justify="right")
        table.add_column("Date Range")
        for coll, groups in breakdown.items():
            if isinstance(groups, str):
                table.add_row(coll, Text(groups, style=ERR), "", "", "")
                continue
            for cons_label, info in groups.items():
                table.add_row(
                    coll,
                    cons_label,
                    str(info.get("count", "")),
                    str(info.get("periods", "")),
                    Text(str(info.get("date_range", "")), style=LABEL),
                )
        console.print(_panel("Financial Breakdown", table))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def render_metrics(data: Dict[str, Any]) -> None:
    symbol = data.get("symbol", "")
    price = data.get("current_price")

    header = Text()
    header.append(str(symbol), style=HEADER)
    if price is not None:
        header.append("  ", style=VALUE)
        header.append(fmt_amount(price), style=ACCENT)
    header.append("\n", style="")
    header.append(
        Text(
            "  ".join(
                part
                for part in [
                    f"period: {data.get('period_end_date') or '—'}",
                    f"consolidated: {'yes' if data.get('consolidated') else 'no'}",
                    f"filing_type: {data.get('filing_type') or '—'}",
                ]
            ),
            style=LABEL,
        )
    )
    console.print(header)

    placed = set()
    for section, keys in METRIC_GROUPS.items():
        rows = []
        for key in keys:
            if key in placed or key not in data or data[key] is None:
                continue
            placed.add(key)
            label = METRIC_LABELS.get(key, humanize_key(key))
            rows.append((label, fmt_metric_value(key, data[key])))
        if rows:
            console.print(_panel(section, _kv_table(rows)))

    extra = [
        k
        for k in data
        if k not in placed and k not in _META_KEYS and data[k] is not None
    ]
    if extra:
        rows = [
            (METRIC_LABELS.get(k, humanize_key(k)), fmt_metric_value(k, data[k]))
            for k in extra
        ]
        console.print(_panel("Additional", _kv_table(rows)))


# ---------------------------------------------------------------------------
# Announcements & shareholdings
# ---------------------------------------------------------------------------


def render_announcements(data: Dict[str, Any]) -> None:
    header = Text()
    header.append("ANNOUNCEMENTS  ", style=LABEL)
    header.append(str(data.get("symbol", "")), style=HEADER)
    header.append(f"   market: {data.get('market', 'equities')}", style=LABEL)
    console.print(Panel(header, border_style=MUTED))

    items = data.get("announcements") or []
    if not items:
        console.print(Text("No announcements.", style=LABEL))
        return

    table = Table(header_style=HEADER, border_style=MUTED, row_styles=["", "dim"])
    table.add_column("Date")
    table.add_column("Heading")
    table.add_column("Category", style=LABEL)
    table.add_column("XBRL", justify="center")
    table.add_column("Attachment", style=LABEL)
    for a in items:
        attachment = a.get("attachment") or ""
        if len(attachment) > 60:
            attachment = attachment[:57] + "..."
        table.add_row(
            str(a.get("date") or "—"),
            str(a.get("heading") or "—"),
            str(a.get("category") or "—"),
            "yes" if a.get("has_xbrl") else "no",
            attachment,
        )
    console.print(table)
    console.print(Text(f"{len(items)} announcements", style=LABEL))


def render_shareholdings(data: Dict[str, Any]) -> None:
    header = Text()
    header.append("SHAREHOLDINGS  ", style=LABEL)
    header.append(str(data.get("symbol", "")), style=HEADER)
    holdings = data.get("shareholdings") or {}
    if holdings.get("period_end_date"):
        header.append(f"   period: {holdings['period_end_date']}", style=LABEL)
    console.print(Panel(header, border_style=MUTED))

    rows = []
    for key, label in _SHAREHOLDING_LABELS.items():
        value = holdings.get(key)
        if value is None:
            continue
        f = _to_float(value)
        display = Text(
            f"{f:.2f}%" if f is not None else "—", style=VALUE if f is None else POS
        )
        rows.append((label, display))
    if not rows:
        console.print(Text("No shareholding data.", style=LABEL))
        return
    console.print(_kv_table(rows))


# ---------------------------------------------------------------------------
# Utilities / errors
# ---------------------------------------------------------------------------


def render_not_implemented(name: str) -> None:
    line = Text()
    line.append(name.upper(), style=HEADER)
    line.append("   not yet implemented", style=LABEL)
    console.print(Panel(line, title=f"[bold white]{name}[/]", border_style=MUTED))


def render_error(title: str, message: str) -> None:
    panel = Panel(
        Text(str(message), style=VALUE),
        title=f"[bold red]{title}[/]",
        border_style=ERR,
    )
    error_console.print(panel)


def render_panel_text(title: str, text: str) -> None:
    console.print(
        Panel(Text(text), title=f"[bold white]{title}[/]", border_style=MUTED)
    )


def render_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str, indent=2))

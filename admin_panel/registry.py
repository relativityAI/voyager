"""Curated registry of Voyager API endpoints for the Playground tab.

Mirrors api.py and src/auth/routes.py. ``auth`` levels:
    public — no key; key — any X-API-Key; write — needs data:write scope;
    admin — X-Voyager-Admin-Key.

Param ``type`` values rendered by app.py:
    text / symbol   text input (symbol joins the autocomplete quick-pick)
    select          selectbox (options may be scalars or {label, value})
    bool            checkbox
    int             number input
    json_body       textarea (sent as the request body)
    scopes          multiselect (sent as json body key)
"""

from typing import Any, Dict, List

Endpoint = Dict[str, Any]

PUBLIC: List[Endpoint] = [
    {
        "name": "Health check",
        "group": "Health",
        "method": "GET",
        "path": "/",
        "auth": "public",
        "description": 'Simple liveness: returns {"ok": 1}.',
    },
    {
        "name": "Liveness probe",
        "group": "Health",
        "method": "GET",
        "path": "/healthz",
        "auth": "public",
        "description": "Always-true health probe (Render checks this).",
    },
    {
        "name": "Readiness probe",
        "group": "Health",
        "method": "GET",
        "path": "/readyz",
        "auth": "public",
        "description": "Checks the DB; 503 when MongoDB is unreachable.",
    },
    {
        "name": "Prometheus metrics",
        "group": "Health",
        "method": "GET",
        "path": "/metrics",
        "auth": "public",
        "description": "Prometheus text exposition (requests, durations, process).",
    },
    {
        "name": "LLM guide (llms.txt)",
        "group": "Health",
        "method": "GET",
        "path": "/llms.txt",
        "auth": "public",
        "description": "llms.txt markdown guide for AI agents: auth, conventions, quick start.",
    },
]


def _symbol_params() -> List[Dict[str, Any]]:
    return [
        {"name": "symbol", "type": "symbol", "required": True},
        {
            "name": "source",
            "type": "select",
            "default": "nse",
            "options": ["nse", "sec"],
        },
    ]


def _statement_params() -> List[Dict[str, Any]]:
    return _symbol_params() + [
        {
            "name": "consolidated",
            "type": "bool3",
            "default": "null",
            "options": [
                {"label": "Consolidated (true)", "value": "true"},
                {"label": "Standalone (false)", "value": "false"},
                {"label": "Both (null)", "value": "null"},
            ],
        },
        {
            "name": "filing_type",
            "type": "select",
            "default": "quarterly",
            "options": ["quarterly", "annual"],
        },
        {
            "name": "limit",
            "type": "int",
            "default": 0,
            "min": 0,
            "help": "Number of rows (0 = all).",
        },
        {"name": "all_fields", "type": "bool", "default": False},
    ]


LIST: List[Endpoint] = [
    {
        "name": "List categories",
        "group": "Explore",
        "method": "GET",
        "path": "/search-categories",
        "auth": "key",
        "description": "Available sources / countries.",
        "params": [
            {
                "name": "category",
                "type": "select",
                "default": "sources",
                "options": ["sources", "countries", "industries", "sectors", "indices"],
            },
            {
                "name": "source",
                "type": "select",
                "default": "nse",
                "options": ["nse", "sec"],
            },
        ],
    },
    {
        "name": "Search symbols",
        "group": "Explore",
        "method": "GET",
        "path": "/search-symbol",
        "auth": "key",
        "description": "Case-insensitive substring search over the stock data ledger.",
        "params": [
            {"name": "q", "type": "text", "required": True, "help": "e.g. 'relian'"},
            {
                "name": "source",
                "type": "select",
                "default": "nse",
                "options": ["nse", "sec"],
            },
            {"name": "limit", "type": "int", "default": 20, "min": 1, "max": 50},
        ],
    },
]

DATA: List[Endpoint] = [
    {
        "name": "Merged financials",
        "group": "Financial statements",
        "method": "GET",
        "path": "/financials",
        "auth": "key",
        "description": "Latest income + balance + cash-flow merged into one doc.",
        "params": _symbol_params()
        + [
            {"name": "consolidated", "type": "bool", "default": True},
            {
                "name": "filing_type",
                "type": "select",
                "default": "quarterly",
                "options": ["quarterly", "annual"],
            },
            {
                "name": "all_fields",
                "type": "bool",
                "default": False,
                "help": "Return all stored fields instead of only priority metrics.",
            },
            {
                "name": "history",
                "type": "bool",
                "default": False,
                "help": "Add all stored periods, merged per period on both reporting bases.",
            },
        ],
    },
    {
        "name": "Income statements",
        "group": "Financial statements",
        "method": "GET",
        "path": "/financials/income-statements",
        "auth": "key",
        "description": "Raw income statement rows from the DB.",
        "params": _statement_params(),
    },
    {
        "name": "Balance sheets",
        "group": "Financial statements",
        "method": "GET",
        "path": "/financials/balance-sheets",
        "auth": "key",
        "description": "Raw balance sheet rows from the DB.",
        "params": _statement_params(),
    },
    {
        "name": "Cash flows",
        "group": "Financial statements",
        "method": "GET",
        "path": "/financials/cash-flows",
        "auth": "key",
        "description": "Raw cash flow rows from the DB.",
        "params": _statement_params(),
    },
    {
        "name": "Financial metrics",
        "group": "Computed metrics",
        "method": "GET",
        "path": "/financial-metrics",
        "auth": "key",
        "description": "All financial metrics in one call: TTM flows + latest-quarter balance sheet. Response uses last_quarter_end_date / last_annual_end_date; all values rounded to 2 decimals.",
        "params": _symbol_params()
        + [
            {"name": "consolidated", "type": "bool", "default": True},
            {
                "name": "filing_type",
                "type": "select",
                "default": "ttm",
                "options": ["ttm", "quarterly", "annual"],
                "help": "ttm (default) computes flows over the trailing twelve months; quarterly/annual are overrides.",
            },
            {
                "name": "fields",
                "type": "text",
                "default": "",
                "help": "Comma-separated metric names to keep (identifier meta always kept).",
            },
        ],
    },
    {
        "name": "Financial metrics batch",
        "group": "Computed metrics",
        "method": "GET",
        "path": "/financial-metrics/batch",
        "auth": "key",
        "description": "Computed metrics for up to 10 symbols in one call; a bad symbol reports per-symbol error without failing the batch.",
        "params": [
            {
                "name": "symbols",
                "type": "text",
                "required": True,
                "help": "Comma-separated, e.g. RELIANCE,TCS",
            },
            {
                "name": "source",
                "type": "select",
                "default": "nse",
                "options": ["nse", "sec"],
            },
            {"name": "consolidated", "type": "bool", "default": True},
            {
                "name": "filing_type",
                "type": "select",
                "default": "ttm",
                "options": ["ttm", "quarterly", "annual"],
            },
            {
                "name": "fields",
                "type": "text",
                "default": "",
                "help": "Comma-separated metric names to keep.",
            },
        ],
    },
    {
        "name": "Announcements",
        "group": "Corporate actions",
        "method": "GET",
        "path": "/announcements",
        "auth": "key",
        "description": "Corporate announcements: NSE announcements, or 8-K filings for SEC/EDGAR.",
        "params": _symbol_params()
        + [
            {
                "name": "market",
                "type": "select",
                "default": "equities",
                "options": ["equities", "sme"],
            },
        ],
    },
    {
        "name": "Shareholdings",
        "group": "Corporate actions",
        "method": "GET",
        "path": "/shareholdings",
        "auth": "key",
        "description": "All shareholding periods, newest first (promoter / FII / DII / public, NSE); US insider-ownership schema (SEC).",
        "params": _symbol_params(),
    },
]

ADVANCED: List[Endpoint] = [
    {
        "name": "Technicals report",
        "group": "Price",
        "method": "GET",
        "path": "/technicals",
        "auth": "key",
        "description": "End-to-end technical analysis report: 61 sections across multi-timeframe indicators, raw OHLCV, structure, patterns, levels, scenarios. Each section degrades to 'unsupported' rather than failing.",
        "params": _symbol_params()
        + [
            {
                "name": "timeframes",
                "type": "text",
                "default": "daily,weekly,monthly",
                "help": "Comma-separated: intraday, daily, weekly, monthly",
            },
        ],
    },
    {
        "name": "Technicals chart (PNG)",
        "group": "Price",
        "method": "GET",
        "path": "/technicals/chart",
        "auth": "key",
        "description": "Rendered price chart image (close + SMA20/50/200 + volume) for chart-reading skills.",
        "params": _symbol_params()
        + [
            {"name": "timeframe", "type": "select", "options": ["daily", "weekly", "monthly"], "default": "weekly"},
            {"name": "bars", "type": "number", "default": "160", "help": "20-400 bars"},
        ],
    },
    {
        "name": "Price history (OHLCV)",
        "group": "Price",
        "method": "GET",
        "path": "/history",
        "auth": "key",
        "description": "Raw OHLCV bars from the price provider (daily, or intraday intervals).",
        "params": _symbol_params()
        + [
            {
                "name": "period",
                "type": "select",
                "default": "1y",
                "options": ["3mo", "6mo", "1y", "2y", "5y", "max"],
            },
            {
                "name": "interval",
                "type": "text",
                "default": "1d",
                "help": "1d (default) or intraday like 5m, 15m, 1h",
            },
        ],
    },
    {
        "name": "Stored prices (OHLCV)",
        "group": "Price",
        "method": "GET",
        "path": "/prices/ohlcv",
        "auth": "key",
        "description": "OHLCV price history read from the DB (paginated).",
        "params": [
            {"name": "symbol", "type": "symbol", "required": True},
            {"name": "exchange", "type": "text", "required": True, "help": "Real exchange name: NSE, BSE, NYSE, NASDAQ"},
            {"name": "country", "type": "select", "default": "in", "options": ["in", "us"]},
            {"name": "interval", "type": "select", "default": "daily", "options": ["daily", "weekly", "monthly"]},
            {"name": "start_date", "type": "text", "help": "Start date YYYY-MM-DD"},
            {"name": "end_date", "type": "text", "help": "End date YYYY-MM-DD"},
            {"name": "limit", "type": "int", "default": 500, "min": 1, "max": 5000},
            {"name": "offset", "type": "int", "default": 0, "min": 0},
        ],
    },
    {
        "name": "News stories",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/news/stories",
        "auth": "key",
        "description": "Latest RSS news stories for a market (cached).",
        "params": [
            {
                "name": "country",
                "type": "select",
                "default": "in",
                "options": ["in", "us"],
            },
            {"name": "days", "type": "int", "default": 7, "min": 1, "max": 30},
            {"name": "limit", "type": "int", "default": 20, "min": 1, "max": 50},
        ],
    },
    {
        "name": "News by ticker",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/news/ticker",
        "auth": "key",
        "description": "News stories mentioning a stock symbol.",
        "params": [
            {"name": "symbol", "type": "symbol", "required": True},
            {
                "name": "country",
                "type": "select",
                "default": "in",
                "options": ["in", "us"],
            },
            {"name": "days", "type": "int", "default": 7, "min": 1, "max": 30},
        ],
    },
    {
        "name": "Document pages",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/documents/pages",
        "auth": "key",
        "description": "Per-page text for selected pages of a PDF previously cached via the ingest tooling.",
        "params": [
            {
                "name": "url",
                "type": "text",
                "required": True,
                "help": "PDF URL or local path.",
            },
            {
                "name": "pages",
                "type": "text",
                "required": True,
                "help": '1-based page spec, e.g. "3,5-9".',
            },
            {"name": "symbol", "type": "symbol", "default": ""},
            {
                "name": "source",
                "type": "select",
                "default": "nse",
                "options": ["nse", "sec"],
            },
        ],
    },
    {
        "name": "Reddit mentions",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/social/reddit",
        "auth": "key",
        "description": "Search Reddit for a ticker/company mention.",
        "params": [
            {"name": "query", "type": "text", "required": True},
            {"name": "limit", "type": "int", "default": 10, "min": 1, "max": 50},
        ],
    },
    {
        "name": "YouTube search",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/social/youtube/search",
        "auth": "key",
        "description": "Search YouTube for a ticker/company (e.g. earnings call).",
        "params": [
            {"name": "query", "type": "text", "required": True},
            {"name": "limit", "type": "int", "default": 15, "min": 1, "max": 50},
        ],
    },
    {
        "name": "YouTube transcript",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/social/youtube/transcript",
        "auth": "key",
        "description": "Fetch the transcript (captions) for a YouTube video.",
        "params": [
            {"name": "video_id", "type": "text", "required": True},
        ],
    },
]

def _macro_country() -> Dict[str, Any]:
    return {
        "name": "country",
        "type": "select",
        "default": "in",
        "options": ["in", "us"],
        "help": "Only 'in' is connected; 'us' returns 501.",
    }


def _macro_dates() -> List[Dict[str, Any]]:
    return [
        {"name": "start_date", "type": "text", "default": "", "help": "YYYY-MM-DD"},
        {"name": "end_date", "type": "text", "default": "", "help": "YYYY-MM-DD"},
    ]


def _macro_index_params() -> List[Dict[str, Any]]:
    return [
        _macro_country(),
        {
            "name": "symbol",
            "type": "symbol",
            "required": True,
            "in_path": True,
            "default": "NIFTY 50",
            "help": "NSE index symbol, e.g. 'NIFTY 50'.",
        },
    ]


MACRO: List[Endpoint] = [
    {
        "name": "Macro overview",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/overview",
        "auth": "key",
        "description": "One-shot dashboard: top indices, FII/DII flows, turnover, repo rate.",
        "params": [_macro_country()],
    },
    {
        "name": "Index universe",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/indices",
        "auth": "key",
        "description": "Full NSE index universe with live snapshot, valuation and breadth.",
        "params": [
            _macro_country(),
            {"name": "limit", "type": "int", "default": 50, "min": 1, "max": 300},
        ],
    },
    {
        "name": "Index snapshot",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/indices/{symbol}",
        "auth": "key",
        "description": "Live snapshot for a single index.",
        "params": _macro_index_params(),
    },
    {
        "name": "Index history",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/indices/{symbol}/history",
        "auth": "key",
        "description": "Daily OHLC history for an index.",
        "params": _macro_index_params() + _macro_dates(),
    },
    {
        "name": "Index valuation",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/indices/{symbol}/valuation",
        "auth": "key",
        "description": "Historical PE/PB/DY for an index.",
        "params": _macro_index_params() + _macro_dates(),
    },
    {
        "name": "Index total returns",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/indices/{symbol}/returns",
        "auth": "key",
        "description": "Total-return (TRI) history for an index.",
        "params": _macro_index_params() + _macro_dates(),
    },
    {
        "name": "Index constituents",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/indices/{symbol}/constituents",
        "auth": "key",
        "description": "Constituent stocks of an index.",
        "params": _macro_index_params(),
    },
    {
        "name": "Market valuation",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/valuation",
        "auth": "key",
        "description": "Whole-market per-equity P/E. 503 when the upstream CSV is missing (by design).",
        "params": [
            _macro_country(),
            {"name": "date", "type": "text", "default": "", "help": "Trade date YYYY-MM-DD"},
        ],
    },
    {
        "name": "Market breadth",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/breadth",
        "auth": "key",
        "description": "Advances/declines, top gainers and losers.",
        "params": [
            _macro_country(),
            {"name": "limit", "type": "int", "default": 10, "min": 1, "max": 50},
        ],
    },
    {
        "name": "FII/DII flows",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/flows",
        "auth": "key",
        "description": "FII/DII daily cash-market net flows.",
        "params": [_macro_country()],
    },
    {
        "name": "NSDL FPI flows",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/flows/fpi",
        "auth": "key",
        "description": "NSDL FPI flows. 503 unless headless Chromium is available upstream.",
        "params": [_macro_country()],
    },
    {
        "name": "Cash turnover",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/turnover",
        "auth": "key",
        "description": "Cash-market turnover by segment.",
        "params": [_macro_country()],
    },
    {
        "name": "F&O option chain",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/derivatives",
        "auth": "key",
        "description": "Option-chain summary: OI, PCR, max-pain.",
        "params": [
            _macro_country(),
            {
                "name": "symbol",
                "type": "text",
                "required": True,
                "default": "NIFTY",
                "help": "Underlying, e.g. NIFTY or BANKNIFTY",
            },
        ],
    },
    {
        "name": "RBI rates",
        "group": "Macro",
        "method": "GET",
        "path": "/macro/rates",
        "auth": "key",
        "description": "RBI current policy rates and reference FX.",
        "params": [_macro_country()],
    },
]

# API-key management has no HTTP routes; the panel's API Keys tab
# talks to the DB directly.

ALL_ENDPOINTS: List[Endpoint] = PUBLIC + LIST + DATA + ADVANCED + MACRO

AUTH_LABELS = {
    "public": "public",
    "key": "key",
}

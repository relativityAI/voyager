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
        "group": "Lists",
        "method": "GET",
        "path": "/list",
        "auth": "key",
        "description": "Available sources / countries / industries / sectors / indices.",
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
        "group": "Lists",
        "method": "GET",
        "path": "/search",
        "auth": "key",
        "description": "Case-insensitive substring search over symbols in the DB, ranked by data coverage.",
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

PULLS: List[Endpoint] = [
    {
        "name": "Submit pull",
        "group": "Pulls",
        "method": "POST",
        "path": "/pull",
        "auth": "write",
        "description": "Submit an async XBRL pull job (returns 202 + job_id).",
        "params": [
            {"name": "symbol", "type": "symbol", "required": True},
            {
                "name": "source",
                "type": "select",
                "default": "nse",
                "options": ["nse", "sec"],
            },
            {
                "name": "filing_type",
                "type": "select",
                "default": "quarterly",
                "options": ["quarterly", "annual"],
            },
            {
                "name": "refresh",
                "type": "bool",
                "default": False,
                "help": "Re-download and re-parse XBRL already present in the DB.",
            },
        ],
    },
    {
        "name": "Pull status",
        "group": "Pulls",
        "method": "GET",
        "path": "/pull",
        "auth": "key",
        "description": "Pull history, record counts and date coverage per collection.",
        "params": _symbol_params(),
    },
    {
        "name": "Pull job status",
        "group": "Pulls",
        "method": "GET",
        "path": "/pull/jobs/{job_id}",
        "auth": "write",
        "description": "Poll the status/result of a pull job.",
        "params": [
            {"name": "job_id", "type": "text", "required": True, "in_path": True}
        ],
    },
    {
        "name": "List pull jobs",
        "group": "Pulls",
        "method": "GET",
        "path": "/pull/jobs",
        "auth": "write",
        "description": "Recent pull jobs, newest first.",
        "params": [
            {"name": "limit", "type": "int", "default": 20, "min": 1, "max": 100}
        ],
    },
    {
        "name": "Cancel pull job",
        "group": "Pulls",
        "method": "DELETE",
        "path": "/pull/jobs/{job_id}",
        "auth": "write",
        "description": "Cancel a queued/running job to free its concurrency slot (stuck-job escape hatch).",
        "params": [
            {"name": "job_id", "type": "text", "required": True, "in_path": True}
        ],
    },
]

ADVANCED: List[Endpoint] = [
    {
        "name": "Technicals report",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/technicals",
        "auth": "key",
        "description": "End-to-end technical analysis report: multi-timeframe indicators, structure, patterns, levels, scenarios. Each section degrades to 'unsupported' rather than failing.",
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
        "name": "Price history (OHLCV)",
        "group": "Advanced Data Suite",
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
        "name": "Parse document",
        "group": "Advanced Data Suite",
        "method": "POST",
        "path": "/documents/parse",
        "auth": "write",
        "description": "Build + cache a PageIndex tree for a PDF (async job, 202).",
        "params": [
            {
                "name": "url",
                "type": "text",
                "required": True,
                "help": "PDF URL or path to structure.",
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
        "name": "Document index",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/documents/{document_id}/index",
        "auth": "key",
        "description": "Get the cached PageIndex tree for a parsed document.",
        "params": [
            {
                "name": "document_id",
                "type": "int",
                "default": 1,
                "min": 1,
                "in_path": True,
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

ADMIN: List[Endpoint] = [
    {
        "name": "Create API key",
        "group": "API keys",
        "method": "POST",
        "path": "/admin/keys",
        "auth": "admin",
        "description": "Create a service key. Raw key is returned once only.",
        "body": {
            "name": "my-app",
            "owner": "service",
            "scopes": ["data:read"],
            "rpm": 60,
            "expires_in_days": None,
        },
    },
    {
        "name": "List API keys",
        "group": "API keys",
        "method": "GET",
        "path": "/admin/keys",
        "auth": "admin",
        "description": "List keys (prefixes only — hashes never returned).",
    },
    {
        "name": "Get API key",
        "group": "API keys",
        "method": "GET",
        "path": "/admin/keys/{key_id}",
        "auth": "admin",
        "description": "One key by prefix (hash never returned).",
        "params": [
            {
                "name": "key_id",
                "type": "text",
                "required": True,
                "in_path": True,
                "help": "The vgr_… prefix shown in the keys table.",
            }
        ],
    },
    {
        "name": "Revoke API key",
        "group": "API keys",
        "method": "DELETE",
        "path": "/admin/keys/{prefix}",
        "auth": "admin",
        "params": [
            {
                "name": "prefix",
                "type": "text",
                "required": True,
                "in_path": True,
                "help": "The vgr_… prefix shown in the keys table.",
            }
        ],
    },
    {
        "name": "Enable API key",
        "group": "API keys",
        "method": "POST",
        "path": "/admin/keys/{prefix}/enable",
        "auth": "admin",
        "params": [
            {"name": "prefix", "type": "text", "required": True, "in_path": True}
        ],
    },
]

ALL_ENDPOINTS: List[Endpoint] = PUBLIC + LIST + DATA + PULLS + ADVANCED + ADMIN

AUTH_LABELS = {
    "public": "public",
    "key": "key",
    "write": "data:write",
    "admin": "admin key",
}

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
            "default": "true",
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
        "description": "Valuation, profitability, growth, solvency, per-share metrics.",
        "params": _symbol_params()
        + [
            {"name": "consolidated", "type": "bool", "default": True},
            {
                "name": "filing_type",
                "type": "select",
                "default": "quarterly",
                "options": ["quarterly", "annual", "ttm"],
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
        "description": "Latest promoter / FII / DII / public holding (NSE), or a US-specific insider-ownership schema (SEC).",
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
]

ADVANCED: List[Endpoint] = [
    {
        "name": "DCF valuation",
        "group": "Advanced Data Suite",
        "method": "GET",
        "path": "/dcf",
        "auth": "key",
        "description": "Two-stage discounted cash flow valuation from stored data.",
        "params": _symbol_params()
        + [
            {
                "name": "growth_rate",
                "type": "text",
                "default": "",
                "help": "Stage-1 FCF growth 0–0.5 (default: revenue growth).",
            },
            {
                "name": "terminal_growth_rate",
                "type": "text",
                "default": "0.04",
                "help": "Terminal growth 0–0.1.",
            },
            {
                "name": "discount_rate",
                "type": "text",
                "default": "",
                "help": "WACC/discount rate 0–0.5 (default: CAPM cost of equity).",
            },
            {"name": "years", "type": "int", "default": 5, "min": 1, "max": 20},
            {"name": "beta", "type": "text", "default": "1.0"},
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
    {
        "name": "Management sentiment",
        "group": "Advanced Data Suite",
        "method": "POST",
        "path": "/sentiment/management",
        "auth": "write",
        "description": "Analyze management commentary for facts (async job, 202).",
        "params": [
            {
                "name": "url",
                "type": "text",
                "default": "",
                "help": "PDF/transcript URL to analyze.",
            },
            {
                "name": "text",
                "type": "text",
                "default": "",
                "help": "Or, raw text to analyze.",
            },
            {"name": "symbol", "type": "symbol", "default": ""},
            {
                "name": "source",
                "type": "select",
                "default": "nse",
                "options": ["nse", "sec"],
            },
            {
                "name": "model",
                "type": "text",
                "default": "",
                "help": "LiteLLM model override.",
            },
        ],
    },
]

DUMMY: List[Endpoint] = [
    {
        "name": "Funds (not implemented)",
        "group": "Placeholders",
        "method": "GET",
        "path": "/funds",
        "auth": "key",
    },
    {
        "name": "Macro (not implemented)",
        "group": "Placeholders",
        "method": "GET",
        "path": "/macro",
        "auth": "key",
    },
    {
        "name": "News (not implemented)",
        "group": "Placeholders",
        "method": "GET",
        "path": "/news",
        "auth": "key",
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

ALL_ENDPOINTS: List[Endpoint] = PUBLIC + LIST + DATA + PULLS + ADVANCED + DUMMY + ADMIN

AUTH_LABELS = {
    "public": "public",
    "key": "key",
    "write": "data:write",
    "admin": "admin key",
}

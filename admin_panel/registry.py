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
        "description": "Simple liveness: returns {\"ok\": 1}.",
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
        {"name": "country", "type": "text", "default": "in"},
        {"name": "source", "type": "select", "default": "nse", "options": ["nse"]},
    ]


def _statement_params() -> List[Dict[str, Any]]:
    return _symbol_params() + [
        {"name": "consolidated", "type": "bool3", "default": "true",
         "options": [
             {"label": "Consolidated (true)", "value": "true"},
             {"label": "Standalone (false)", "value": "false"},
             {"label": "Both (null)", "value": "null"},
         ]},
        {"name": "filing_type", "type": "select", "default": "quarterly",
         "options": ["quarterly", "annual"]},
        {"name": "limit", "type": "int", "default": 0, "min": 0,
         "help": "Number of rows (0 = all)."},
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
            {"name": "category", "type": "select", "default": "sources",
             "options": ["sources", "countries", "industries", "sectors", "indices"]},
            {"name": "country", "type": "text", "default": "in"},
            {"name": "source", "type": "select", "default": "nse", "options": ["nse"]},
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
        "params": _symbol_params() + [
            {"name": "consolidated", "type": "bool", "default": True},
            {"name": "filing_type", "type": "select", "default": "quarterly",
             "options": ["quarterly", "annual"]},
            {"name": "all_fields", "type": "bool", "default": False,
             "help": "Return all stored fields instead of only priority metrics."},
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
        "params": _symbol_params() + [
            {"name": "consolidated", "type": "bool", "default": True},
            {"name": "filing_type", "type": "select", "default": "quarterly",
             "options": ["quarterly", "annual", "ttm"]},
        ],
    },
    {
        "name": "Announcements",
        "group": "Corporate actions",
        "method": "GET",
        "path": "/announcements",
        "auth": "key",
        "description": "Corporate announcements from NSE.",
        "params": _symbol_params() + [
            {"name": "market", "type": "select", "default": "equities",
             "options": ["equities", "sme"]},
        ],
    },
    {
        "name": "Shareholdings",
        "group": "Corporate actions",
        "method": "GET",
        "path": "/shareholdings",
        "auth": "key",
        "description": "Latest promoter / FII / DII / public holding pattern.",
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
            {"name": "country", "type": "text", "default": "in"},
            {"name": "source", "type": "select", "default": "nse", "options": ["nse"]},
            {"name": "filing_type", "type": "select", "default": "quarterly",
             "options": ["quarterly", "annual"]},
            {"name": "refresh", "type": "bool", "default": False,
             "help": "Re-download and re-parse XBRL already present in the DB."},
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
        "params": [{"name": "job_id", "type": "text", "required": True, "in_path": True}],
    },
    {
        "name": "List pull jobs",
        "group": "Pulls",
        "method": "GET",
        "path": "/pull/jobs",
        "auth": "write",
        "description": "Recent pull jobs, newest first.",
        "params": [{"name": "limit", "type": "int", "default": 20, "min": 1, "max": 100}],
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
        "params": [{"name": "prefix", "type": "text", "required": True, "in_path": True,
                    "help": "The vgr_… prefix shown in the keys table."}],
    },
    {
        "name": "Enable API key",
        "group": "API keys",
        "method": "POST",
        "path": "/admin/keys/{prefix}/enable",
        "auth": "admin",
        "params": [{"name": "prefix", "type": "text", "required": True, "in_path": True}],
    },
]

ALL_ENDPOINTS: List[Endpoint] = PUBLIC + LIST + DATA + PULLS + DUMMY + ADMIN

AUTH_LABELS = {"public": "public", "key": "key", "write": "data:write", "admin": "admin key"}

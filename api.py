import asyncio
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute
from loguru import logger

from __version__ import __version__
from src.auth import APIKey, require_api_key, require_scope
from src.auth.routes import router as admin_router
from src.db.engine import init_db, ping_database
from src.jobs import (
    JobNotCancellable,
    PullLimitReached,
    cancel_job,
    drain_forever,
    get_job,
    list_jobs,
    reap_forever,
    reap_stale_jobs,
    requeue_orphaned_running,
    submit_pull,
    submit_task,
)
from src.logging_config import setup_logging
from src.observability import (
    HttpCacheMiddleware,
    PrometheusMiddleware,
    init_sentry,
    metrics_enabled,
    metrics_response,
)
from src.schemas import (
    Announcements,
    Doc,
    HistoryResponse,
    JobAccepted,
    JobPublic,
    KeyPublic,
    ListResponse,
    MacroConstituents,
    MacroDerivatives,
    MacroFlows,
    MacroHistory,
    MacroIndexQuoteOrSnapshot,
    MacroIndices,
    MacroOverview,
    MacroRates,
    MacroTurnover,
    MacroValuationOrBreadth,
    MetricsBatch,
    OkResponse,
    SearchResponse,
    Snapshot,
    StatementPage,
)
from src.services import (
    InvalidRequestError,
    ServiceError,
    financial_metrics,
    get_announcements,
    get_financials,
    get_news_stories,
    get_pull_status,
    get_reddit,
    get_shareholdings,
    get_statement_data,
    get_ticker_mentions,
    get_youtube_search,
    get_youtube_transcript,
    list_category,
    macro_breadth,
    macro_derivatives,
    macro_flows,
    macro_flows_fpi,
    macro_index_constituents,
    macro_index_history,
    macro_index_quote,
    macro_index_returns,
    macro_index_valuation,
    macro_indices,
    macro_market_valuation,
    macro_overview,
    macro_rates,
    macro_turnover,
    search_symbols,
)
from src.services._common import _validate_source
from src.services.documents import get_document_index
from src.services.technical_report import build_technical_report

# Shared query-copy so every route describes the same parameter the same way
# (agents read these descriptions as tool docs).
_SYM = "Exchange ticker in UPPER CASE, e.g. 'RELIANCE'."
_SRC = "Data source: 'nse' (NSE India, default) or 'sec' (US SEC/EDGAR); country is derived from it. Unknown source -> 501."
_QA = "Reporting frequency: 'quarterly' or 'annual'."


load_dotenv()

setup_logging()

init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_db()
    # Running rows from the previous process are orphans: requeue them so the
    # drain loop resumes them, then reap anything genuinely hung. Startup alone
    # is not enough: Render keeps serving the old instance when a deploy fails
    # its health check, so the reaper also runs on a timer.
    await requeue_orphaned_running()
    await reap_stale_jobs()
    reaper = asyncio.create_task(reap_forever())
    drainer = asyncio.create_task(drain_forever())
    try:
        yield
    finally:
        reaper.cancel()
        drainer.cancel()


openapi_tags = [
    {"name": "System", "description": "Health, liveness, readiness and metrics probes."},
    {"name": "Lists", "description": "Enumerations of available categories (sources, countries, etc.)."},
    {"name": "Financials", "description": "Financial statements and computed financial metrics."},
    {"name": "Macro", "description": "India macro market endpoints: index universe, valuations, flows, turnover, F&O and rates."},
    {"name": "Corporate Actions", "description": "Corporate announcements and shareholding patterns."},
    {"name": "Data Pulls", "description": "Pull raw data from the exchange and track async pull jobs."},
    {"name": "Advanced Data Suite", "description": "News, social signals and document structuring."},
    {"name": "Admin", "description": "API key management (guarded by VOYAGER_ADMIN_KEY)."},
]

agent_guide = """\
**Auth:** `X-API-Key: <key>` or `Authorization: Bearer <key>` (scopes `data:read`, `data:write`). Admin routes use `X-Voyager-Admin-Key`.

**Errors:** RFC 9457 `application/problem+json` — `detail` (human-readable string), `code` (stable machine token), `retry` + `retry_after_seconds` when waiting may help. Validation failures (422) list each bad field in `errors`.

**Conventions:** dates `YYYY-MM-DD`; timestamps ISO-8601 with timezone (bar dates are exchange-local); money in INR (`source=sec` -> USD); ratios are percent where `17.0` means 17%; a key omitted from an object means "not reported" (endpoints offering `all_fields=true` return explicit nulls instead). List `limit` caps are noted per query (max 50; batches max 10 symbols). Rate limits are reported in `X-RateLimit-*` headers, with `429` beyond them.

**Consolidation fallback:** `consolidated=true` returns consolidated statements, except for periods where the issuer only filed standalone — those fall back to the standalone filing, the whole-company picture at that time. Charts from this data are therefore continuous even when an issuer's reporting basis changes mid-history.

**Async pulls:** `POST /pull` returns `202 {job_id, status_url}`; poll `status_url` until `status` is `done` or `failed`.\
"""

app = FastAPI(
    title="Voyager",
    version=__version__,
    openapi_tags=openapi_tags,
    description=agent_guide,
    lifespan=lifespan,
)

_cors_origins = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(PrometheusMiddleware)
app.add_middleware(HttpCacheMiddleware)

app.include_router(admin_router)


# --- RFC 9457 problem responses ---------------------------------------------
# `detail` stays a string so pre-existing clients that read `.detail` keep
# working; `code`/`retry` are the stable machine fields agents branch on.
_RETRYABLE = {429, 502, 503}
_STATUS_TITLE = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
}
_STATUS_CODE = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    501: "not_implemented",
    502: "upstream_error",
    503: "service_unavailable",
}
_SERVICE_CODE = {
    "InvalidRequestError": "invalid_request",
    "NotFoundError": "not_found",
    "UnsupportedCountryError": "country_not_supported",
    "UnsupportedSourceError": "unsupported_source",
    "ServiceUnavailableError": "service_unavailable",
    "UpstreamError": "upstream_error",
}


def _problem(
    status_code: int,
    detail,
    code: Optional[str] = None,
    headers: Optional[dict] = None,
    errors=None,
) -> JSONResponse:
    code = code or _STATUS_CODE.get(status_code, "error")
    body = {
        "type": f"/problems/{code}",
        "title": _STATUS_TITLE.get(status_code, "Error"),
        "status": status_code,
        "detail": str(detail) if detail is not None else "",
        "code": code,
        "retry": status_code in _RETRYABLE,
    }
    if headers and headers.get("Retry-After"):
        try:
            body["retry_after_seconds"] = int(headers["Retry-After"])
        except ValueError:
            pass
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(
        status_code=status_code,
        content=body,
        headers=headers,
        media_type="application/problem+json",
    )


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return _problem(
        exc.status_code,
        exc.message,
        code=_SERVICE_CODE.get(type(exc).__name__, "internal_error"),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail, code = exc.detail, None
    if isinstance(detail, dict):  # auth errors: {code, message}
        code, detail = detail.get("code"), detail.get("message", "")
    elif isinstance(detail, list):  # any stray legacy 422 list
        detail = "; ".join(str(d) for d in detail)
    return _problem(exc.status_code, detail, code=code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    errors = jsonable_encoder(exc.errors())
    lines = [
        f"{'/'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}"
        for e in errors
    ]
    return _problem(422, "; ".join(lines), code="validation_error", errors=errors)


# --- OpenAPI: auth schemes + typed success schemas --------------------------
# FastAPI only emits `security` when routes use Security(); ours is
# Depends(Header)-based, so the spec is patched after generation.
# ponytail: auth detection matches dependency callables by __qualname__ —
# add a prefix here if a route ever gains a new auth dependency.
_PROBLEM_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "description": "Problem type URI, e.g. /problems/not_found"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string", "description": "Human-readable message"},
        "code": {"type": "string", "description": "Stable machine token; branch on this"},
        "retry": {"type": "boolean", "description": "True when retrying later may succeed"},
        "retry_after_seconds": {"type": "integer"},
        "errors": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["status", "detail", "code"],
}


def _walk_auth(dependant, found: list) -> None:
    qn = getattr(dependant.call, "__qualname__", "") or ""
    if qn == "require_admin_key":
        req = [{"AdminHeader": []}]
    elif qn.startswith(("require_api_key", "get_current_api_key", "require_scope")):
        req = [{"ApiKeyHeader": []}, {"BearerAuth": []}]
    else:
        req = None
    if req and req not in found:
        found.append(req)
    for sub in dependant.dependencies:
        _walk_auth(sub, found)


def _model_schema(model, components: dict) -> dict:
    js = model.model_json_schema(ref_template="#/components/schemas/{model}")
    components.update(js.pop("$defs", {}) or {})
    components[model.__name__] = js
    return {"$ref": f"#/components/schemas/{model.__name__}"}


# (method, path) -> model (or raw schema dict) for the success response.
_RESPONSE_MODELS = {
    ("GET", "/"): OkResponse,
    ("GET", "/healthz"): OkResponse,
    ("GET", "/readyz"): OkResponse,
    ("GET", "/metrics"): OkResponse,
    ("GET", "/llms.txt"): {"type": "string", "description": "llms.txt agent guide"},
    ("GET", "/list"): ListResponse,
    ("GET", "/search"): SearchResponse,
    ("GET", "/financials"): Snapshot,
    ("GET", "/financials/income-statements"): StatementPage,
    ("GET", "/financials/balance-sheets"): StatementPage,
    ("GET", "/financials/cash-flows"): StatementPage,
    ("POST", "/pull"): JobAccepted,
    ("GET", "/pull"): Snapshot,
    ("GET", "/pull/jobs/{job_id}"): JobPublic,
    ("DELETE", "/pull/jobs/{job_id}"): JobPublic,
    ("GET", "/pull/jobs"): {
        "type": "array",
        "items": {"$ref": "#/components/schemas/JobPublic"},
    },
    ("GET", "/history"): HistoryResponse,
    ("GET", "/technicals"): Snapshot,
    ("GET", "/technicals/chart"): {
        "type": "string",
        "format": "binary",
        "description": "PNG chart image (close + SMA20/50/200 + volume)",
    },
    ("GET", "/financial-metrics"): Snapshot,
    ("GET", "/financial-metrics/batch"): MetricsBatch,
    ("GET", "/macro/overview"): MacroOverview,
    ("GET", "/macro/indices"): MacroIndices,
    ("GET", "/macro/indices/{symbol}"): MacroIndexQuoteOrSnapshot,
    ("GET", "/macro/indices/{symbol}/history"): MacroHistory,
    ("GET", "/macro/indices/{symbol}/valuation"): MacroHistory,
    ("GET", "/macro/indices/{symbol}/returns"): MacroHistory,
    ("GET", "/macro/indices/{symbol}/constituents"): MacroConstituents,
    ("GET", "/macro/valuation"): MacroValuationOrBreadth,
    ("GET", "/macro/breadth"): MacroValuationOrBreadth,
    ("GET", "/macro/flows"): MacroFlows,
    ("GET", "/macro/flows/fpi"): MacroValuationOrBreadth,
    ("GET", "/macro/turnover"): MacroTurnover,
    ("GET", "/macro/derivatives"): MacroDerivatives,
    ("GET", "/macro/rates"): MacroRates,
    ("GET", "/announcements"): Announcements,
    ("GET", "/shareholdings"): Snapshot,
    ("GET", "/news/stories"): Doc,
    ("GET", "/news/ticker"): Doc,
    ("POST", "/documents/parse"): JobAccepted,
    ("GET", "/documents/{document_id}/index"): Doc,
    ("GET", "/social/reddit"): Doc,
    ("GET", "/social/youtube/search"): Doc,
    ("GET", "/social/youtube/transcript"): Doc,
    ("GET", "/admin/keys"): {
        "type": "array",
        "items": {"$ref": "#/components/schemas/KeyPublic"},
    },
    ("GET", "/admin/keys/{key_id}"): KeyPublic,
    ("DELETE", "/admin/keys/{key_id}"): KeyPublic,
    ("POST", "/admin/keys/{key_id}/enable"): KeyPublic,
}

_openapi_base = app.openapi


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = _openapi_base()
    comps = schema.setdefault("components", {})
    comps["securitySchemes"] = {
        "ApiKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Service API key (the same key is also accepted as Bearer).",
        },
        "BearerAuth": {"type": "http", "scheme": "bearer"},
        "AdminHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Voyager-Admin-Key",
            "description": "Server-side VOYAGER_ADMIN_KEY; admin key management only.",
        },
    }
    schemas = comps.setdefault("schemas", {})

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            if method in ("HEAD", "OPTIONS"):
                continue
            op = schema["paths"].get(route.path, {}).get(method.lower())
            if not op:
                continue
            found: list = []
            _walk_auth(route.dependant, found)
            if found:
                op["security"] = found

    for (method, path), model in _RESPONSE_MODELS.items():
        op = schema["paths"].get(path, {}).get(method.lower())
        if not op:
            continue
        for code in ("200", "201", "202"):
            if code in op.get("responses", {}):
                schema_dict = (
                    model if isinstance(model, dict) else _model_schema(model, schemas)
                )
                op["responses"][code].setdefault("content", {})[
                    "application/json"
                ] = {"schema": schema_dict}
                break

    # Errors are problem+json everywhere; swap FastAPI's default 422 list shape.
    for path_item in schema["paths"].values():
        for op in path_item.values():
            if not isinstance(op, dict):
                continue
            for code, resp in (op.get("responses") or {}).items():
                if str(code)[0] in "45":
                    resp["content"] = {
                        "application/problem+json": {"schema": _PROBLEM_SCHEMA}
                    }

    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


# Meta keys never stripped by ?fields= — a filtered response still identifies
# the stock, its periods and its data availability.
_META_FIELDS = {
    "symbol",
    "source",
    "consolidated",
    "filing_type",
    "last_quarter_end_date",
    "last_annual_end_date",
    "last_annual_end_date_source",
    "ttm_window_complete",
    "ttm_quarters_used",
    "statement_periods",
    "data_available",
    "price_data",
    "data_quality",
}


def _filter_fields(data: dict, fields: Optional[str]) -> dict:
    """Keep only the requested metric fields (+ meta) when ?fields= is given."""
    if not fields or not data:
        return data
    wanted = {f.strip() for f in fields.split(",") if f.strip()}
    if not wanted:
        return data
    return {
        k: v for k, v in data.items() if k in _META_FIELDS or k in wanted
    }


@app.get("/", summary="Health check", tags=["System"])
def ping():
    return {"ok": 1}


@app.get("/healthz", summary="Liveness probe", tags=["System"])
def healthz():
    return {"ok": True}


@app.get("/readyz", summary="Readiness probe (checks DB)", tags=["System"])
async def readyz():
    if not await ping_database():
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"ok": True}


if metrics_enabled():

    @app.get("/metrics", summary="Prometheus metrics", tags=["System"])
    async def metrics():
        return metrics_response()


LLMS_TXT = """\
# Voyager

> Financial data API: NSE (India) and US SEC/EDGAR — financial statements and
> computed metrics, prices/OHLCV, corporate announcements, shareholding
> patterns, technical reports, news/social signals, and async data pulls.

Voyager serves a machine-readable contract first. An agent should read the
OpenAPI spec, then this page for conventions.

## Docs

- [OpenAPI 3.1 spec](/openapi.json): every endpoint, auth scheme, enums and response schemas.
- [Swagger UI](/docs) / [ReDoc](/redoc): interactive reference.
- [agent_endpoints.md](https://github.com/relativityAI/voyager/blob/main/docs/agent_endpoints.md): endpoint guide with worked examples.
- [README](https://github.com/relativityAI/voyager#endpoints): endpoint table and setup.

## Auth

- Data endpoints: `X-API-Key: <key>` or `Authorization: Bearer <key>` (scopes `data:read`, `data:write`).
- Admin key management: `X-Voyager-Admin-Key`.

## Conventions

- Dates `YYYY-MM-DD`; timestamps ISO-8601 with timezone (bar dates are exchange-local).
- Money in INR unless `source=sec` (USD). Ratios are percent (`17.0` = 17%).
- A key omitted from an object means "not reported"; `all_fields=true` returns explicit nulls instead.
- Errors: RFC 9457 `application/problem+json` with `detail` (string) and `code` (stable token); `retry`/`retry_after_seconds` say whether to wait. 422 adds per-field `errors`.
- Rate limits: `X-RateLimit-*` headers, `429` beyond them.
- Consolidation fallback: `consolidated=true` returns consolidated statements, except periods where the issuer only filed standalone — those fall back to the standalone filing (the whole-company picture at that time), so charts stay continuous when reporting basis changes mid-history.

## Quick start

    curl -H "X-API-Key: $VOYAGER_API_KEY" \\
      "http://localhost:8001/financial-metrics?symbol=RELIANCE"

    curl -H "X-API-Key: $VOYAGER_API_KEY" \\
      "http://localhost:8001/search?q=relian"

Async pull: `POST /pull?symbol=X&filing_type=quarterly` -> `202 {job_id,
status_url}`; poll `status_url` until `status` is `done` or `failed`.
"""


@app.get("/llms.txt", summary="LLM/agent guide (llms.txt)", tags=["System"])
def llms_txt():
    return PlainTextResponse(LLMS_TXT, media_type="text/markdown")


@app.get(
    "/list",
    summary="List available categories",
    tags=["Lists"],
    dependencies=[Depends(require_api_key)],
)
def list_category_endpoint(
    category: Literal["sources", "countries", "industries", "sectors", "indices"] = Query(
        "sources",
        description="Category to enumerate",
    ),
    source: str = Query("nse", description=_SRC),
):
    return list_category(category, source=source)


@app.get(
    "/search",
    summary="Search symbols stored in the DB (case-insensitive substring match)",
    tags=["Lists"],
    dependencies=[Depends(require_api_key)],
)
async def search_endpoint(
    q: str = Query(..., min_length=2, description="Search text, e.g. 'relian'"),
    source: str = Query("nse", description=_SRC),
    limit: int = Query(20, ge=1, le=50),
):
    return await search_symbols(q, None, source, limit)


@app.get(
    "/financials",
    summary="Get merged financial data (income, balance, cash flow) for a stock",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financials(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    consolidated: bool = Query(
        True,
        description=(
            "Consolidated (default). Falls back to standalone for statements/periods "
            "where the issuer filed no consolidated data — that filing was the whole "
            "company picture at that time. Use history=true for the full archive."
        ),
    ),
    filing_type: Literal["quarterly", "annual"] = Query(
        "quarterly", description=_QA
    ),
    all_fields: bool = Query(
        False, description="Return all stored fields instead of only priority metrics"
    ),
    history: bool = Query(
        False,
        description="Add all stored periods, merged per period on both reporting bases",
    ),
    report_period_gte: str = Query(None, description="Start period end date (YYYY-MM-DD)"),
    report_period_lte: str = Query(None, description="End period end date (YYYY-MM-DD)"),
):
    return await get_financials(
        symbol,
        None,
        source,
        consolidated,
        filing_type,
        all_fields,
        history,
        report_period_gte=report_period_gte,
        report_period_lte=report_period_lte,
    )


@app.get(
    "/financials/income-statements",
    summary="Fetch income statement data from DB",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financials_income_statements(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    consolidated: Optional[bool] = Query(
        None,
        description=(
            "Both bases (default). true = consolidated only, except periods that "
            "only exist standalone — those fall back to the standalone filing (the "
            "whole company picture at that time). false = standalone only."
        ),
    ),
    filing_type: Literal["quarterly", "annual"] = Query(
        "quarterly", description=_QA
    ),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0, description="Rows to skip (pagination)"),
    all_fields: bool = Query(
        False, description="Return all stored fields instead of only priority metrics"
    ),
    report_period_gte: str = Query(None, description="Start period end date (YYYY-MM-DD)"),
    report_period_lte: str = Query(None, description="End period end date (YYYY-MM-DD)"),
):
    return await get_statement_data(
        "income-statements",
        symbol,
        None,
        source,
        consolidated,
        filing_type,
        limit,
        offset,
        all_fields,
        report_period_gte=report_period_gte,
        report_period_lte=report_period_lte,
    )


@app.get(
    "/financials/balance-sheets",
    summary="Fetch balance sheet data from DB",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financials_balance_sheets(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    consolidated: Optional[bool] = Query(
        None,
        description=(
            "Both bases (default). true = consolidated only, except periods that "
            "only exist standalone — those fall back to the standalone filing (the "
            "whole company picture at that time). false = standalone only."
        ),
    ),
    filing_type: Literal["quarterly", "annual"] = Query(
        "quarterly", description=_QA
    ),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0, description="Rows to skip (pagination)"),
    all_fields: bool = Query(False),
    report_period_gte: str = Query(None, description="Start period end date (YYYY-MM-DD)"),
    report_period_lte: str = Query(None, description="End period end date (YYYY-MM-DD)"),
):
    return await get_statement_data(
        "balance-sheets",
        symbol,
        None,
        source,
        consolidated,
        filing_type,
        limit,
        offset,
        all_fields,
        report_period_gte=report_period_gte,
        report_period_lte=report_period_lte,
    )


@app.get(
    "/financials/cash-flows",
    summary="Fetch cash flow data from DB",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financials_cash_flows(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    consolidated: Optional[bool] = Query(
        None,
        description=(
            "Both bases (default). true = consolidated only, except periods that "
            "only exist standalone — those fall back to the standalone filing (the "
            "whole company picture at that time). false = standalone only."
        ),
    ),
    filing_type: Literal["quarterly", "annual"] = Query(
        "quarterly", description=_QA
    ),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0, description="Rows to skip (pagination)"),
    all_fields: bool = Query(False),
    report_period_gte: str = Query(None, description="Start period end date (YYYY-MM-DD)"),
    report_period_lte: str = Query(None, description="End period end date (YYYY-MM-DD)"),
):
    return await get_statement_data(
        "cash-flows",
        symbol,
        None,
        source,
        consolidated,
        filing_type,
        limit,
        offset,
        all_fields,
        report_period_gte=report_period_gte,
        report_period_lte=report_period_lte,
    )


@app.post(
    "/pull",
    summary="Pull raw stock data from exchange into DB (async job)",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Data Pulls"],
)
async def financials_pull(
    symbol: str = Query(..., description=_SYM),
    key: APIKey = Depends(require_scope("data:write")),
    source: str = Query("nse", description=_SRC),
    filing_type: Literal["quarterly", "annual"] = Query(
        "quarterly", description=_QA
    ),
    refresh: bool = Query(
        False, description="Re-download and re-parse XBRL already present in the DB"
    ),
):
    symbol = symbol.upper()

    country, source = _validate_source(None, source)
    try:
        job = await submit_pull(
            symbol, filing_type, refresh, created_by=key.prefix,
            country=country, source=source,
        )
    except PullLimitReached as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "job_id": job.job_id,
        "status": job.status,
        "status_url": f"/pull/jobs/{job.job_id}",
    }


@app.get(
    "/pull",
    summary="Get pull status and data availability for a stock",
    tags=["Data Pulls"],
    dependencies=[Depends(require_api_key)],
)
async def financials_pull_status(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
):
    return await get_pull_status(symbol, None, source)


@app.get(
    "/pull/jobs/{job_id}",
    summary="Get the status/result of an async pull job",
    tags=["Data Pulls"],
    dependencies=[Depends(require_scope("data:write"))],
)
async def pull_job_status(job_id: str, key: APIKey = Depends(require_scope("data:write"))):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pull job not found")
    return job.to_public_dict()


@app.delete(
    "/pull/jobs/{job_id}",
    summary="Cancel a queued/running job (frees its concurrency slot)",
    tags=["Data Pulls"],
)
async def pull_job_cancel(job_id: str, key: APIKey = Depends(require_scope("data:write"))):
    try:
        job = await cancel_job(job_id, created_by=key.prefix)
    except ValueError:
        raise HTTPException(status_code=404, detail="Pull job not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except JobNotCancellable as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return job.to_public_dict()


@app.get(
    "/pull/jobs",
    summary="List recent pull jobs",
    tags=["Data Pulls"],
    dependencies=[Depends(require_scope("data:write"))],
)
async def pull_job_list(limit: int = Query(20, ge=1, le=100)):
    jobs = await list_jobs(limit)
    return [j.to_public_dict() for j in jobs]


@app.get(
    "/history",
    summary="Raw OHLCV price history (daily, or intraday via interval)",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def price_history(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    period: Literal["3mo", "6mo", "1y", "2y", "5y", "max"] = Query(
        "1y",
        description="Lookback window: 3mo, 6mo, 1y, 2y, 5y or max",
    ),
    interval: str = Query(
        "1d",
        description="Bar size: 1d (default) or an intraday interval like 5m, 15m, 1h",
    ),
):
    from src.services._common import InvalidRequestError
    from src.tools.nse.technicals import fetch_history

    _, source_u = _validate_source(None, source)
    if source_u != "NSE":
        raise InvalidRequestError("/history currently supports source=nse")
    hist = await asyncio.to_thread(fetch_history, symbol.upper(), "NSE", period, interval)
    if hist is None or hist.empty:
        raise HTTPException(status_code=404, detail=f"No price history for {symbol.upper()}")
    rows = [
        {
            "date": ts.isoformat(),
            "open": float(r["Open"]),
            "high": float(r["High"]),
            "low": float(r["Low"]),
            "close": float(r["Close"]),
            "volume": int(r["Volume"]) if r.get("Volume") == r.get("Volume") else None,
        }
        for ts, r in hist.iterrows()
    ]
    return {
        "symbol": symbol.upper(),
        "period": period,
        "interval": interval,
        "bars": len(rows),
        "data": rows,
    }


@app.get(
    "/technicals",
    summary=(
        "End-to-end technical analysis report: 61 sections covering "
        "multi-timeframe indicators, raw OHLCV, structure, patterns, "
        "levels and scenarios"
    ),
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def technicals_report(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    timeframes: str = Query(
        "daily,weekly,monthly",
        description=(
            "Comma-separated subset of: intraday, daily, weekly, monthly. "
            "Unsupported sections degrade to 'unsupported' with a reason."
        ),
    ),
):
    _, source_u = _validate_source(None, source)
    if source_u != "NSE":
        raise InvalidRequestError("/technicals currently supports source=nse")
    announcements = await get_announcements(symbol, None, source, "equities")
    ann_items = announcements.get("announcements") if isinstance(announcements, dict) else None
    return await build_technical_report(
        symbol.upper(), source_u, timeframes, ann_items
    )


@app.get(
    "/technicals/chart",
    summary="Weekly/daily price chart PNG (close + SMA20/50/200 + volume)",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def technicals_chart(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    timeframe: Literal["daily", "weekly", "monthly"] = Query(
        "weekly", description="Chart timeframe (weekly is the analysis default)"
    ),
    bars: int = Query(160, ge=20, le=400, description="Number of bars to render"),
):
    from fastapi.responses import Response

    from src.services.technical_report import render_chart_png

    _, source_u = _validate_source(None, source)
    if source_u != "NSE":
        raise InvalidRequestError("/technicals/chart currently supports source=nse")
    try:
        png = await asyncio.to_thread(
            render_chart_png, symbol.upper(), "NSE", timeframe, bars
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png")


@app.get(
    "/financial-metrics",
    summary="Retrieve computed financial metrics for a stock",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financial_metrics_endpoint(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    consolidated: bool = Query(
        True, description="True for consolidated, False for standalone"
    ),
    filing_type: Literal["quarterly", "annual", "ttm"] = Query(
        "ttm", description="Window: 'quarterly', 'annual' or 'ttm' (trailing twelve months)"
    ),
    report_period_gte: str = Query(None, description="Start period end date (YYYY-MM-DD)"),
    report_period_lte: str = Query(None, description="End period end date (YYYY-MM-DD)"),
    limit: int = Query(None, ge=1, description="Max periods to return when range provided"),
    fields: str = Query(
        None,
        description="Comma-separated metric names to keep (e.g. 'price_to_earnings_ratio,return_on_equity'). Unknown names are ignored; price/identifier meta is always kept.",
    ),
):
    data = await financial_metrics(
        symbol,
        None,
        source,
        consolidated,
        filing_type,
        report_period_gte=report_period_gte,
        report_period_lte=report_period_lte,
        limit=limit,
    )
    if isinstance(data, list):
        return [_filter_fields(d, fields) for d in data]
    return _filter_fields(data, fields)


@app.get(
    "/financial-metrics/batch",
    summary="Computed financial metrics for several symbols in one call",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financial_metrics_batch(
    symbols: str = Query(
        ..., description="Comma-separated symbols, e.g. 'RELIANCE,TCS' (max 10)"
    ),
    source: str = Query("nse", description=_SRC),
    consolidated: bool = Query(True),
    filing_type: Literal["quarterly", "annual", "ttm"] = Query(
        "ttm", description="Window: 'quarterly', 'annual' or 'ttm' (trailing twelve months)"
    ),
    fields: str = Query(None, description="Comma-separated metric names to keep"),
):
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise InvalidRequestError("Provide at least one symbol")
    if len(wanted) > 10:
        raise InvalidRequestError("Max 10 symbols per batch call")
    results = {}
    for sym in wanted:
        try:
            results[sym] = _filter_fields(
                await financial_metrics(sym, None, source, consolidated, filing_type),
                fields,
            )
        except ServiceError as exc:
            results[sym] = {"error": exc.message, "status": exc.status_code}
    return {
        "source": source.lower(),
        "consolidated": consolidated,
        "filing_type": filing_type,
        "count": len(results),
        "metrics": results,
    }


@app.get(
    "/announcements",
    summary="Fetch corporate announcements for a stock",
    tags=["Corporate Actions"],
    dependencies=[Depends(require_api_key)],
)
async def announcements(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
    market: Literal["equities", "sme"] = Query(
        "equities", description="Market segment"
    ),
):
    return await get_announcements(symbol, None, source, market)


@app.get(
    "/shareholdings",
    summary="Fetch shareholding pattern for a stock (parsed from XBRL)",
    tags=["Corporate Actions"],
    dependencies=[Depends(require_api_key)],
)
async def shareholdings(
    symbol: str = Query(..., description=_SYM),
    source: str = Query("nse", description=_SRC),
):
    return await get_shareholdings(symbol, None, source)


@app.get(
    "/news/stories",
    summary="Latest news stories for a market (RSS, cached)",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def news_stories(
    country: Literal["us", "in"] = Query("in", description="Market"),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(20, ge=1, le=50),
):
    return await get_news_stories(country, days, limit)


@app.get(
    "/news/ticker",
    summary="News stories mentioning a stock symbol",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def news_ticker(
    symbol: str = Query(..., description=_SYM),
    country: Literal["us", "in"] = Query("in", description="Market"),
    days: int = Query(7, ge=1, le=30),
):
    return await get_ticker_mentions(symbol, country, days)


@app.post(
    "/documents/parse",
    summary="Build + cache a PageIndex tree for a PDF (async job)",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Advanced Data Suite"],
)
async def documents_parse(
    url: str = Query(..., description="PDF URL or path to structure"),
    symbol: str = Query(None),
    source: str = Query("nse"),
    callback_url: str = Query(
        None, description="Optional webhook URL; POSTed the job result on completion"
    ),
    key: APIKey = Depends(require_scope("data:write")),
):
    try:
        job = await submit_task(
            "documents.parse",
            {"url": url, "symbol": symbol, "source": source, "callback_url": callback_url},
            created_by=key.prefix,
        )
    except PullLimitReached as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "job_id": job.job_id,
        "status": job.status,
        "status_url": f"/pull/jobs/{job.job_id}",
    }


@app.get(
    "/documents/{document_id}/index",
    summary="Get the cached PageIndex tree for a document",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def document_index(document_id: int):
    return await get_document_index(document_id)


@app.get(
    "/social/reddit",
    summary="Search Reddit for a ticker/company mention",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def social_reddit(
    query: str,
    limit: int = Query(10, ge=1, le=50),
):
    return await get_reddit(query, limit)


@app.get(
    "/social/youtube/search",
    summary="Search YouTube for a ticker/company (e.g. earnings call)",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def social_youtube_search(
    query: str,
    limit: int = Query(15, ge=1, le=50),
):
    return await get_youtube_search(query, limit)


@app.get(
    "/social/youtube/transcript",
    summary="Fetch the transcript (captions) for a YouTube video",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def social_youtube_transcript(
    video_id: str,
):
    return await get_youtube_transcript(video_id)


_COUNTRY = Query("in", pattern=r"^[A-Za-z]{2}$", description="ISO-3166 alpha-2. Only 'in' is connected.")
_MACRO_SYM = "NSE index symbol, URL-encoded (e.g. 'NIFTY 50')."
_MACRO_DATES = "Window (YYYY-MM-DD). Default: trailing 1 year."


@app.get(
    "/macro/overview",
    summary="One-shot macro dashboard: top indices, FII/DII flows, turnover, repo rate",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_overview_endpoint(country: str = _COUNTRY):
    return await macro_overview(country)


@app.get(
    "/macro/indices",
    summary="Full NSE index universe with live snapshot, valuation and breadth",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_indices_endpoint(
    country: str = _COUNTRY,
    limit: int = Query(None, ge=1, le=300, description="Max indices to return"),
):
    return await macro_indices(country, limit)


@app.get(
    "/macro/indices/{symbol}",
    summary="Live snapshot for a single index",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_index_quote_endpoint(symbol: str, country: str = _COUNTRY):
    return await macro_index_quote(country, symbol)


@app.get(
    "/macro/indices/{symbol}/history",
    summary="Daily OHLC history for an index",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_index_history_endpoint(
    symbol: str,
    country: str = _COUNTRY,
    start_date: str = Query(None, description=_MACRO_DATES),
    end_date: str = Query(None, description=_MACRO_DATES),
):
    return await macro_index_history(country, symbol, start_date, end_date)


@app.get(
    "/macro/indices/{symbol}/valuation",
    summary="Historical PE/PB/DY for an index",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_index_valuation_endpoint(
    symbol: str,
    country: str = _COUNTRY,
    start_date: str = Query(None, description=_MACRO_DATES),
    end_date: str = Query(None, description=_MACRO_DATES),
):
    return await macro_index_valuation(country, symbol, start_date, end_date)


@app.get(
    "/macro/indices/{symbol}/returns",
    summary="Total-return (TRI) history for an index",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_index_returns_endpoint(
    symbol: str,
    country: str = _COUNTRY,
    start_date: str = Query(None, description=_MACRO_DATES),
    end_date: str = Query(None, description=_MACRO_DATES),
):
    return await macro_index_returns(country, symbol, start_date, end_date)


@app.get(
    "/macro/indices/{symbol}/constituents",
    summary="Constituent stocks of an index",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_index_constituents_endpoint(symbol: str, country: str = _COUNTRY):
    return await macro_index_constituents(country, symbol)


@app.get(
    "/macro/valuation",
    summary="Whole-market per-equity P/E",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_market_valuation_endpoint(
    country: str = _COUNTRY,
    date: str = Query(None, description="Trade date (YYYY-MM-DD)"),
):
    return await macro_market_valuation(country, date)


@app.get(
    "/macro/breadth",
    summary="Market breadth: advances/declines, top gainers and losers",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_breadth_endpoint(
    country: str = _COUNTRY,
    limit: int = Query(10, ge=1, le=50, description="Top gainers/losers to return"),
):
    return await macro_breadth(country, limit)


@app.get(
    "/macro/flows",
    summary="FII/DII daily cash-market net flows",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_flows_endpoint(country: str = _COUNTRY):
    return await macro_flows(country)


@app.get(
    "/macro/flows/fpi",
    summary="NSDL FPI flows (requires headless Chromium upstream)",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_flows_fpi_endpoint(country: str = _COUNTRY):
    return await macro_flows_fpi(country)


@app.get(
    "/macro/turnover",
    summary="Cash-market turnover by segment",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_turnover_endpoint(country: str = _COUNTRY):
    return await macro_turnover(country)


@app.get(
    "/macro/derivatives",
    summary="F&O option-chain summary: OI, PCR, max-pain",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_derivatives_endpoint(
    symbol: str = Query(..., description="Underlying symbol, e.g. 'NIFTY' or 'BANKNIFTY'"),
    country: str = _COUNTRY,
):
    return await macro_derivatives(country, symbol)


@app.get(
    "/macro/rates",
    summary="RBI current policy rates and reference FX",
    tags=["Macro"],
    dependencies=[Depends(require_api_key)],
)
async def macro_rates_endpoint(country: str = _COUNTRY):
    return await macro_rates(country)


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8001)),
        reload=os.getenv("ENVIRONMENT", "development").lower() == "development",
    )

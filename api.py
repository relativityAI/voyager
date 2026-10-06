import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
    search_symbols,
)
from src.services._common import _validate_source
from src.services.documents import get_document_index
from src.services.technical_report import build_technical_report

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
    {"name": "Corporate Actions", "description": "Corporate announcements and shareholding patterns."},
    {"name": "Data Pulls", "description": "Pull raw data from the exchange and track async pull jobs."},
    {"name": "Advanced Data Suite", "description": "News, social signals and document structuring."},
    {"name": "Admin", "description": "API key management (guarded by VOYAGER_ADMIN_KEY)."},
]

app = FastAPI(
    title="Voyager", version=__version__, lifespan=lifespan, openapi_tags=openapi_tags
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


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


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
    if not fields:
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


@app.get(
    "/list",
    summary="List available categories",
    tags=["Lists"],
    dependencies=[Depends(require_api_key)],
)
def list_category_endpoint(
    category: str = Query(
        "sources",
        description="Category: sources, countries, industries, sectors, indices",
    ),
    source: str = Query("nse", description="Data source"),
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
    source: str = Query("nse", description="Data source"),
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
    symbol: str,
    source: str = Query("nse"),
    consolidated: bool = Query(True),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    all_fields: bool = Query(
        False, description="Return all stored fields instead of only priority metrics"
    ),
    history: bool = Query(
        False,
        description="Add all stored periods, merged per period on both reporting bases",
    ),
):
    return await get_financials(
        symbol, None, source, consolidated, filing_type, all_fields, history
    )


@app.get(
    "/financials/income-statements",
    summary="Fetch income statement data from DB",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financials_income_statements(
    symbol: str,
    source: str = Query("nse"),
    consolidated: Optional[bool] = Query(
        None,
        description="Filter by consolidated (true) or standalone (false). Default: both.",
    ),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0, description="Rows to skip (pagination)"),
    all_fields: bool = Query(
        False, description="Return all stored fields instead of only priority metrics"
    ),
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
    )


@app.get(
    "/financials/balance-sheets",
    summary="Fetch balance sheet data from DB",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financials_balance_sheets(
    symbol: str,
    source: str = Query("nse"),
    consolidated: Optional[bool] = Query(
        None, description="Default: both bases."
    ),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0, description="Rows to skip (pagination)"),
    all_fields: bool = Query(False),
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
    )


@app.get(
    "/financials/cash-flows",
    summary="Fetch cash flow data from DB",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financials_cash_flows(
    symbol: str,
    source: str = Query("nse"),
    consolidated: Optional[bool] = Query(
        None, description="Default: both bases."
    ),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0, description="Rows to skip (pagination)"),
    all_fields: bool = Query(False),
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
    )


@app.post(
    "/pull",
    summary="Pull raw stock data from exchange into DB (async job)",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Data Pulls"],
)
async def financials_pull(
    symbol: str,
    key: APIKey = Depends(require_scope("data:write")),
    source: str = Query("nse"),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    refresh: bool = Query(
        False, description="Re-download and re-parse XBRL already present in the DB"
    ),
):
    symbol = symbol.upper()

    if filing_type not in ("quarterly", "annual"):
        raise InvalidRequestError("filing_type must be 'quarterly' or 'annual'")

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
    symbol: str,
    source: str = Query("nse"),
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
    symbol: str,
    source: str = Query("nse"),
    period: str = Query(
        "1y",
        description="3mo, 6mo, 1y, 2y, 5y, or max",
    ),
    interval: str = Query(
        "1d",
        description="1d (default) or an intraday interval like 5m, 15m, 1h",
    ),
):
    from src.tools.nse.technicals import fetch_history
    from src.services._common import InvalidRequestError

    _, source_u = _validate_source(None, source)
    if source_u != "NSE":
        raise InvalidRequestError("/history currently supports source=nse")
    period = period.lower()
    if period not in ("3mo", "6mo", "1y", "2y", "5y", "max"):
        raise InvalidRequestError("period must be one of 3mo, 6mo, 1y, 2y, 5y, max")
    hist = await asyncio.to_thread(fetch_history, symbol.upper(), "NSE", period, interval)
    if hist is None or hist.empty:
        raise HTTPException(status_code=404, detail=f"No price history for {symbol.upper()}")
    rows = [
        {
            "date": str(ts),
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
        "End-to-end technical analysis report: 60 sections covering "
        "multi-timeframe indicators, structure, patterns, levels and scenarios"
    ),
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def technicals_report(
    symbol: str,
    source: str = Query("nse"),
    timeframes: str = Query(
        "daily,weekly,monthly",
        description="Comma-separated: intraday, daily, weekly, monthly",
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
    "/financial-metrics",
    summary="Retrieve computed financial metrics for a stock",
    tags=["Financials"],
    dependencies=[Depends(require_api_key)],
)
async def financial_metrics_endpoint(
    symbol: str,
    source: str = Query("nse"),
    consolidated: bool = Query(
        True, description="True for consolidated, False for standalone"
    ),
    filing_type: str = Query("ttm", description="quarterly, annual, or ttm"),
    fields: str = Query(
        None,
        description="Comma-separated metric names to keep (e.g. 'price_to_earnings_ratio,return_on_equity'). Unknown names are ignored; price/identifier meta is always kept.",
    ),
):
    data = await financial_metrics(symbol, None, source, consolidated, filing_type)
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
    source: str = Query("nse"),
    consolidated: bool = Query(True),
    filing_type: str = Query("ttm", description="quarterly, annual, or ttm"),
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
    symbol: str,
    source: str = Query("nse"),
    market: str = Query("equities", description="Market segment: equities or sme"),
):
    return await get_announcements(symbol, None, source, market)


@app.get(
    "/shareholdings",
    summary="Fetch shareholding pattern for a stock (parsed from XBRL)",
    tags=["Corporate Actions"],
    dependencies=[Depends(require_api_key)],
)
async def shareholdings(
    symbol: str,
    source: str = Query("nse"),
):
    return await get_shareholdings(symbol, None, source)


@app.get(
    "/news/stories",
    summary="Latest news stories for a market (RSS, cached)",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def news_stories(
    country: str = Query("in", description="Market: us or in"),
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
    symbol: str,
    country: str = Query("in", description="Market: us or in"),
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


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8001)),
        reload=os.getenv("ENVIRONMENT", "development").lower() == "development",
    )

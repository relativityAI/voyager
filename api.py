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
from src.db.connection import init_db, ping_database
from src.jobs import (
    PullAlreadyActive,
    PullLimitReached,
    get_job,
    list_jobs,
    reap_stale_jobs,
    submit_pull,
    submit_task,
)
from src.logging_config import setup_logging
from src.observability import (
    PrometheusMiddleware,
    init_observability,
    metrics_enabled,
    metrics_response,
)
from src.services import (
    InvalidRequestError,
    ServiceError,
    dcf_valuation,
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
)
from src.services._common import _validate_source
from src.services.documents import get_document_index

load_dotenv()

setup_logging()

init_observability()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_db()
    await reap_stale_jobs()
    yield


openapi_tags = [
    {"name": "System", "description": "Health, liveness, readiness and metrics probes."},
    {"name": "Lists", "description": "Enumerations of available categories (sources, countries, etc.)."},
    {"name": "Financials", "description": "Financial statements and computed financial metrics."},
    {"name": "Corporate Actions", "description": "Corporate announcements and shareholding patterns."},
    {"name": "Data Pulls", "description": "Pull raw data from the exchange and track async pull jobs."},
    {"name": "Advanced Data Suite", "description": "Valuation, news, social signals, documents and sentiment analysis."},
    {"name": "Admin", "description": "API key management (guarded by VOYAGER_ADMIN_KEY)."},
    {"name": "Coming Soon", "description": "Placeholder endpoints not yet implemented."},
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

app.include_router(admin_router)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


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
):
    return await get_financials(
        symbol, None, source, consolidated, filing_type, all_fields
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
        True,
        description="Filter by consolidated (default true) or standalone (false). Pass null for both.",
    ),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
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
    consolidated: Optional[bool] = Query(True),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
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
    consolidated: Optional[bool] = Query(True),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
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
    except PullAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc))
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
async def pull_job_status(job_id: str):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pull job not found")
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
    filing_type: str = Query("quarterly", description="quarterly, annual, or ttm"),
):
    return await financial_metrics(symbol, None, source, consolidated, filing_type)


@app.get(
    "/dcf",
    summary="Two-stage discounted cash flow valuation from stored data",
    tags=["Advanced Data Suite"],
    dependencies=[Depends(require_api_key)],
)
async def dcf_endpoint(
    symbol: str,
    source: str = Query("nse"),
    growth_rate: Optional[float] = Query(
        None, ge=0, le=0.5, description="Stage-1 FCF growth rate (default: revenue growth)"
    ),
    terminal_growth_rate: Optional[float] = Query(
        None, ge=0, le=0.1, description="Terminal growth rate (default 0.04)"
    ),
    discount_rate: Optional[float] = Query(
        None, ge=0, le=0.5, description="WACC/discount rate (default: CAPM cost of equity)"
    ),
    years: int = Query(5, ge=1, le=20),
    beta: float = Query(1.0),
):
    return await dcf_valuation(
        symbol, source,
        growth_rate=growth_rate,
        terminal_growth_rate=terminal_growth_rate,
        discount_rate=discount_rate,
        years=years,
        beta=beta,
    )


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
    "/funds",
    summary="Fund data (not yet implemented)",
    tags=["Coming Soon"],
    dependencies=[Depends(require_api_key)],
)
def funds():
    return {"status": "not_implemented", "note": "Fund data not yet implemented"}


@app.get(
    "/macro",
    summary="Macroeconomic data (not yet implemented)",
    tags=["Coming Soon"],
    dependencies=[Depends(require_api_key)],
)
def macro():
    return {
        "status": "not_implemented",
        "note": "Macroeconomic data not yet implemented",
    }


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
    key: APIKey = Depends(require_scope("data:write")),
):
    try:
        job = await submit_task(
            "documents.parse",
            {"url": url, "symbol": symbol, "source": source},
            created_by=key.prefix,
        )
    except PullLimitReached as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except PullAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc))
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


@app.post(
    "/sentiment/management",
    summary="Analyze management commentary for facts (async job)",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Advanced Data Suite"],
)
async def sentiment_management_run(
    url: str = Query(None, description="PDF/transcript URL to analyze"),
    text: str = Query(None, description="Or, raw text to analyze"),
    symbol: str = Query(None),
    source: str = Query("nse"),
    model: str = Query(None, description="LiteLLM model override"),
    key: APIKey = Depends(require_scope("data:write")),
):
    if url is None and text is None:
        raise InvalidRequestError("Provide either 'url' or 'text'")
    try:
        job = await submit_task(
            "sentiment.management",
            {
                "url": url,
                "text": text,
                "symbol": symbol,
                "source": source,
                "model": model,
            },
            created_by=key.prefix,
        )
    except PullLimitReached as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except PullAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "job_id": job.job_id,
        "status": job.status,
        "status_url": f"/pull/jobs/{job.job_id}",
    }


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8001)),
        reload=os.getenv("ENVIRONMENT", "development").lower() == "development",
    )

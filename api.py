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
    UnsupportedSourceError,
    financial_metrics,
    get_announcements,
    get_financials,
    get_pull_status,
    get_shareholdings,
    get_statement_data,
    list_category,
)

load_dotenv()

setup_logging()

init_observability()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beanie via root /db...")
    await init_db()
    await reap_stale_jobs()
    yield


app = FastAPI(title="Voyager", version=__version__, lifespan=lifespan)

# CORS (optional). The main app calls Voyager server-to-server, but a browser
# client can be enabled by setting CORS_ORIGINS to a comma-separated list.
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


# ---------------------------------------------------------------
# Public endpoints (no API key required)
# ---------------------------------------------------------------


@app.get("/", summary="Health check")
def ping():
    return {"ok": 1}


@app.get("/healthz", summary="Liveness probe")
def healthz():
    return {"ok": True}


@app.get("/readyz", summary="Readiness probe (checks DB)")
async def readyz():
    if not await ping_database():
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"ok": True}


if metrics_enabled():

    @app.get("/metrics", summary="Prometheus metrics")
    async def metrics():
        return metrics_response()


# ---------------------------------------------------------------
# /list — merged listing for sources, countries, industries, etc.
# ---------------------------------------------------------------


@app.get(
    "/list",
    summary="List available categories",
    dependencies=[Depends(require_api_key)],
)
def list_category_endpoint(
    category: str = Query(
        "sources",
        description="Category: sources, countries, industries, sectors, indices",
    ),
    country: str = Query("in", description="Country code"),
    source: str = Query("nse", description="Data source"),
):
    return list_category(category, country, source)


# ---------------------------------------------------------------
# /financials — raw data & financial statement endpoints
# ---------------------------------------------------------------


@app.get(
    "/financials",
    summary="Get merged financial data (income, balance, cash flow) for a stock",
    dependencies=[Depends(require_api_key)],
)
async def financials(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    consolidated: bool = Query(True),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    all_fields: bool = Query(
        False, description="Return all stored fields instead of only priority metrics"
    ),
):
    return await get_financials(
        symbol, country, source, consolidated, filing_type, all_fields
    )


@app.get(
    "/financials/income-statements",
    summary="Fetch income statement data from DB",
    dependencies=[Depends(require_api_key)],
)
async def financials_income_statements(
    symbol: str,
    country: str = Query("in"),
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
        country,
        source,
        consolidated,
        filing_type,
        limit,
        all_fields,
    )


@app.get(
    "/financials/balance-sheets",
    summary="Fetch balance sheet data from DB",
    dependencies=[Depends(require_api_key)],
)
async def financials_balance_sheets(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    consolidated: Optional[bool] = Query(True),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
    all_fields: bool = Query(False),
):
    return await get_statement_data(
        "balance-sheets",
        symbol,
        country,
        source,
        consolidated,
        filing_type,
        limit,
        all_fields,
    )


@app.get(
    "/financials/cash-flows",
    summary="Fetch cash flow data from DB",
    dependencies=[Depends(require_api_key)],
)
async def financials_cash_flows(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    consolidated: Optional[bool] = Query(True),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
    all_fields: bool = Query(False),
):
    return await get_statement_data(
        "cash-flows",
        symbol,
        country,
        source,
        consolidated,
        filing_type,
        limit,
        all_fields,
    )


# ---------------------------------------------------------------
# /pull — submit async pull jobs (admin/data:write only) & status
# ---------------------------------------------------------------


@app.post(
    "/pull",
    summary="Pull raw stock data from exchange into DB (async job)",
    status_code=status.HTTP_202_ACCEPTED,
)
async def financials_pull(
    symbol: str,
    key: APIKey = Depends(require_scope("data:write")),
    country: str = Query("in"),
    source: str = Query("nse"),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    refresh: bool = Query(
        False, description="Re-download and re-parse XBRL already present in the DB"
    ),
):
    symbol = symbol.upper()
    source = source.upper()

    if filing_type not in ("quarterly", "annual"):
        raise InvalidRequestError("filing_type must be 'quarterly' or 'annual'")

    if country.lower() == "in" and source == "NSE":
        try:
            job = await submit_pull(symbol, filing_type, refresh, created_by=key.prefix)
        except PullLimitReached as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except PullAlreadyActive as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {
            "job_id": job.job_id,
            "status": job.status,
            "status_url": f"/pull/jobs/{job.job_id}",
        }
    raise UnsupportedSourceError(
        f"Source '{source}' for country '{country}' is not yet supported"
    )


@app.get(
    "/pull",
    summary="Get pull status and data availability for a stock",
    dependencies=[Depends(require_api_key)],
)
async def financials_pull_status(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
):
    return await get_pull_status(symbol, country, source)


@app.get(
    "/pull/jobs/{job_id}",
    summary="Get the status/result of an async pull job",
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
    dependencies=[Depends(require_scope("data:write"))],
)
async def pull_job_list(limit: int = Query(20, ge=1, le=100)):
    jobs = await list_jobs(limit)
    return [j.to_public_dict() for j in jobs]


# ---------------------------------------------------------------
# /financial-metrics — computed financial metrics
# ---------------------------------------------------------------


@app.get(
    "/financial-metrics",
    summary="Retrieve computed financial metrics for a stock",
    dependencies=[Depends(require_api_key)],
)
async def financial_metrics_endpoint(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    consolidated: bool = Query(
        True, description="True for consolidated, False for standalone"
    ),
    filing_type: str = Query("quarterly", description="quarterly, annual, or ttm"),
):
    return await financial_metrics(symbol, country, source, consolidated, filing_type)


# ---------------------------------------------------------------
# /announcements — dedicated endpoint
# ---------------------------------------------------------------


@app.get(
    "/announcements",
    summary="Fetch corporate announcements for a stock",
    dependencies=[Depends(require_api_key)],
)
async def announcements(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    market: str = Query("equities", description="Market segment: equities or sme"),
):
    return await get_announcements(symbol, country, source, market)


# ---------------------------------------------------------------
# /shareholdings — dedicated endpoint
# ---------------------------------------------------------------


@app.get(
    "/shareholdings",
    summary="Fetch shareholding pattern for a stock (parsed from XBRL)",
    dependencies=[Depends(require_api_key)],
)
async def shareholdings(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
):
    return await get_shareholdings(symbol, country, source)


# ---------------------------------------------------------------
# /funds — dummy
# ---------------------------------------------------------------


@app.get(
    "/funds",
    summary="Fund data (not yet implemented)",
    dependencies=[Depends(require_api_key)],
)
def funds():
    return {"status": "not_implemented", "note": "Fund data not yet implemented"}


# ---------------------------------------------------------------
# /macro — dummy
# ---------------------------------------------------------------


@app.get(
    "/macro",
    summary="Macroeconomic data (not yet implemented)",
    dependencies=[Depends(require_api_key)],
)
def macro():
    return {
        "status": "not_implemented",
        "note": "Macroeconomic data not yet implemented",
    }


# ---------------------------------------------------------------
# /news — dummy
# ---------------------------------------------------------------


@app.get(
    "/news",
    summary="News data (not yet implemented)",
    dependencies=[Depends(require_api_key)],
)
def news():
    return {"status": "not_implemented", "note": "News data not yet implemented"}


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8001)),
        reload=os.getenv("ENVIRONMENT", "development").lower() == "development",
    )

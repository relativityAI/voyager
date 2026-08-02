import os
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger

from __version__ import __version__
from src.db.connection import init_db
from src.logging_config import setup_logging
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
    pull_nse_data,
)

load_dotenv()

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beanie via root /db...")
    await init_db()
    yield


app = FastAPI(title="Voyager", version=__version__, lifespan=lifespan)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/", summary="Health check")
def ping():
    return {"ok": 1}


# ---------------------------------------------------------------
# /list — merged listing for sources, countries, industries, etc.
# ---------------------------------------------------------------


@app.get("/list", summary="List available categories")
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


@app.get("/financials/income-statements", summary="Fetch income statement data from DB")
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


@app.get("/financials/balance-sheets", summary="Fetch balance sheet data from DB")
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


@app.get("/financials/cash-flows", summary="Fetch cash flow data from DB")
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


@app.post("/pull", summary="Pull raw stock data from exchange into DB")
async def financials_pull(
    symbol: str,
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
        return await pull_nse_data(symbol, filing_type, refresh)
    raise UnsupportedSourceError(
        f"Source '{source}' for country '{country}' is not yet supported"
    )


@app.get("/pull", summary="Get pull status and data availability for a stock")
async def financials_pull_status(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
):
    return await get_pull_status(symbol, country, source)


# ---------------------------------------------------------------
# /financial-metrics — computed financial metrics
# ---------------------------------------------------------------


@app.get(
    "/financial-metrics", summary="Retrieve computed financial metrics for a stock"
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


@app.get("/announcements", summary="Fetch corporate announcements for a stock")
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


@app.get("/funds", summary="Fund data (not yet implemented)")
def funds():
    return {"status": "not_implemented", "note": "Fund data not yet implemented"}


# ---------------------------------------------------------------
# /macro — dummy
# ---------------------------------------------------------------


@app.get("/macro", summary="Macroeconomic data (not yet implemented)")
def macro():
    return {
        "status": "not_implemented",
        "note": "Macroeconomic data not yet implemented",
    }


# ---------------------------------------------------------------
# /news — dummy
# ---------------------------------------------------------------


@app.get("/news", summary="News data (not yet implemented)")
def news():
    return {"status": "not_implemented", "note": "News data not yet implemented"}


if __name__ == "__main__":
    uvicorn.run(
        "api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True
    )

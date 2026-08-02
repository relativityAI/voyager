import asyncio
import csv
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from pymongo.operations import ReplaceOne

from __version__ import __version__
from src.db.connection import get_database, init_db
from src.db.models import NSEStockMetadata
from src.logging_config import setup_logging
from src.tools.nse.client import ENDPOINTS, CookieError, NSEIndia
from src.tools.nse.ratios import FINANCIAL_FIELD_MAP

load_dotenv()

setup_logging()

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "src", "assets")
METRICS_CONFIG_PATH = os.path.join(ASSETS_DIR, "metrics_config.json")

_PRIORITY_CACHE: Dict[str, set] | None = None

def _load_priority_metrics() -> Dict[str, set]:
    global _PRIORITY_CACHE
    if _PRIORITY_CACHE is not None:
        return _PRIORITY_CACHE
    try:
        with open(METRICS_CONFIG_PATH) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}
    result: Dict[str, set] = {}
    for stmt_key, cfg in config.items():
        result[stmt_key] = set(cfg.get("priority", []))
    _PRIORITY_CACHE = result
    return result

STATEMENT_COLLECTIONS: Dict[str, str] = {
    "income_statements": "income_statements",
    "balance_sheets": "balance_sheets",
    "cash_flows": "cash_flows",
    "shareholdings": "shareholdings",
}

COLLECTION_TO_STMT_KEY: Dict[str, str] = {v: k for k, v in STATEMENT_COLLECTIONS.items()}

# Map endpoint route names to collection names
ROUTE_TO_COLLECTION: Dict[str, str] = {
    "income-statements": "income_statements",
    "balance-sheets": "balance_sheets",
    "cash-flows": "cash_flows",
}

ROUTE_TO_PRIORITY_KEY: Dict[str, str] = {
    "income-statements": "income_statements",
    "balance-sheets": "balance_sheets",
    "cash-flows": "cash_flows",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beanie via root /db...")
    await init_db()
    yield


app = FastAPI(title="Voyager", version=__version__, lifespan=lifespan)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "src", "assets")
SOURCES_CSV = os.path.join(ASSETS_DIR, "sources.csv")
COUNTRIES_CSV = os.path.join(ASSETS_DIR, "countries.csv")


def _load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: v.strip() for k, v in row.items()})
    return rows


@app.get("/", summary="Health check")
def ping():
    return {"ok": 1}


# ---------------------------------------------------------------
# /list — merged listing for sources, countries, industries, etc.
# ---------------------------------------------------------------

LIST_PROVIDERS: Dict[str, Any] = {
    "sources": lambda: _load_csv(SOURCES_CSV),
    "countries": lambda: _load_csv(COUNTRIES_CSV),
    "industries": lambda: [],
    "sectors": lambda: [],
    "indices": lambda: [],
}


@app.get("/list", summary="List available categories")
async def list_category(
    category: str = Query("sources", description="Category: sources, countries, industries, sectors, indices"),
    country: str = Query("in", description="Country code"),
    source: str = Query("nse", description="Data source"),
):
    if category.lower() not in LIST_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown category '{category}'. Available: {list(LIST_PROVIDERS.keys())}")
    return {
        "category": category,
        "country": country,
        "source": source.upper(),
        "data": LIST_PROVIDERS[category.lower()](),
    }


# ---------------------------------------------------------------
# NSE pull helpers & constants
# ---------------------------------------------------------------

class PullStockDataRequest(BaseModel):
    source: str = "NSE"
    symbol: str


nse_scraper = NSEIndia(calls_per_second=float(os.getenv("NSE_CALLS_PER_SECOND", "10")))

NSE_MAX_XBRL_CONCURRENCY = int(os.getenv("NSE_MAX_XBRL_CONCURRENCY", "6"))

XBRL_PARSE_MAP: Dict[str, str] = {
    "integrated-filing": "quarterly",
    "quarterly-results": "quarterly",
    "annual-results": "annual",
    "shareholding-pattern": "shareholding",
}

ALL_NSE_COLLECTIONS: Dict[str, str] = {**STATEMENT_COLLECTIONS}


def _fetch_endpoint_json(url: str, symbol: str):
    res = nse_scraper.api._call(url, symbol=symbol)
    if res is None:
        return None
    return nse_scraper.api._safe_json(res)


async def pull_nse_data(
    symbol: str, filing_type: Optional[str] = None, refresh: bool = False
) -> Dict[str, Any]:
    database = get_database()
    total_records = 0
    total_parsed = 0
    endpoint_breakdown: Dict[str, Any] = {}
    raw_by_endpoint: Dict[str, list] = {}
    parsed_counts: Dict[str, int] = {}

    timing: Dict[str, Any] = {"phases": {}, "counts": {}, "total_ms": 0.0}
    _started = time.perf_counter()
    _last = [_started]

    def _tick(key: str) -> None:
        now = time.perf_counter()
        timing["phases"][key] = timing["phases"].get(key, 0.0) + (now - _last[0]) * 1000
        _last[0] = now

    def _count(key: str, n: int = 1) -> None:
        timing["counts"][key] = timing["counts"].get(key, 0) + n

    FT_ENDPOINTS = {
        "quarterly": {"integrated-filing", "quarterly-results"},
        "annual": {"annual-results"},
    }

    selected = [
        (key, url)
        for key, url in ENDPOINTS.items()
        if not (filing_type and key in XBRL_PARSE_MAP and key not in FT_ENDPOINTS.get(filing_type, set()) and key != "shareholding-pattern")
    ]

    _tick("setup")

    # ---- Phase 1: fetch all endpoints (parallel) ----
    urls = [
        url.format(symbol=symbol) if "{symbol}" in url else url
        for _, url in selected
    ]
    fetch_results = await asyncio.gather(
        *[asyncio.to_thread(_fetch_endpoint_json, url, symbol) for url in urls],
        return_exceptions=True,
    )
    _tick("fetch")

    for (ep_key, _), data in zip(selected, fetch_results):
        endpoint_records: list = []
        if isinstance(data, CookieError):
            logger.error(f"Cookie failure for {ep_key} ({symbol}): {data}")
            endpoint_breakdown[ep_key] = "cookie failed"
            raw_by_endpoint[ep_key] = []
            continue
        if isinstance(data, Exception):
            logger.error(f"Error pulling {ep_key} for {symbol}: {data}")
            endpoint_breakdown[ep_key] = str(data)
            raw_by_endpoint[ep_key] = []
            continue
        if data is None:
            logger.warning(f"Endpoint {ep_key} failed ({symbol})")
            endpoint_breakdown[ep_key] = "failed"
            raw_by_endpoint[ep_key] = []
            continue
        if not isinstance(data, (dict, list)) or (isinstance(data, dict) and not data):
            logger.info(f"No data returned for {ep_key} ({symbol})")
            endpoint_breakdown[ep_key] = "no data"
            raw_by_endpoint[ep_key] = []
            continue

        if isinstance(data, dict):
            inner = data.get("data")
            records = inner if isinstance(inner, list) else [data]
        else:
            records = data

        count = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            count += 1
            record["symbol"] = symbol
            endpoint_records.append(record)

        total_records += count
        endpoint_breakdown[ep_key] = count
        raw_by_endpoint[ep_key] = endpoint_records
        _count("endpoint_records", count)
        logger.info(f"Pulled {count} records for {ep_key} ({symbol})")

    # ---- Phase 2: skip XBRL already stored (unless refresh) ----
    existing_urls: set = set()
    if not refresh:
        for coll_name in STATEMENT_COLLECTIONS.values():
            cursor = database[coll_name].find({"symbol": symbol}, {"xbrl_url": 1, "_id": 0})
            async for d in cursor:
                u = d.get("xbrl_url")
                if u:
                    existing_urls.add(u)
    _tick("existing_scan")

    STMT_TO_COLLECTION = {
        "income_statement": "income_statements",
        "balance_sheet": "balance_sheets",
        "cash_flow": "cash_flows",
        "shareholding": "shareholdings",
    }

    seen_xbrl: set = set()
    pending: list = []

    for ep_key, _parse_type in XBRL_PARSE_MAP.items():
        records = raw_by_endpoint.get(ep_key, [])
        if not isinstance(records, list) or not records:
            logger.info(f"No records to parse for {ep_key} ({symbol})")
            continue

        if ep_key == "integrated-filing":
            records = sorted(records, key=lambda r: 1 if r.get("type_Sub") == "Revision" else 0)

        for record in records:
            xbrl_key = record.get("xbrl") or record.get("broadCastDate")
            if not xbrl_key:
                _count("skipped_no_url")
                continue
            if xbrl_key in seen_xbrl:
                _count("skipped_dup_url")
                continue
            if xbrl_key in existing_urls:
                _count("skipped_existing")
                continue
            seen_xbrl.add(xbrl_key)
            pending.append((record, ep_key))

    _tick("dedup")

    # ---- Phase 3: parse XBRL with bounded concurrency ----
    sem = asyncio.Semaphore(NSE_MAX_XBRL_CONCURRENCY)

    async def _parse(record, ep_key):
        async with sem:
            return await asyncio.to_thread(nse_scraper.process_xbrl, record, symbol, ep_key)

    parsed_results = await asyncio.gather(
        *[_parse(record, ep_key) for record, ep_key in pending],
        return_exceptions=True,
    )
    _tick("xbrl")

    # ---- Phase 4: batch DB upserts ----
    ops_by_coll: Dict[str, list] = {}
    for (record, ep_key), parsed in zip(pending, parsed_results):
        if isinstance(parsed, Exception):
            logger.error(f"Error parsing XBRL for {symbol} {ep_key}: {parsed}")
            _count("parse_errors")
            continue
        if parsed is None:
            _count("skipped_unparseable")
            continue

        for stmt_key, coll_name in STMT_TO_COLLECTION.items():
            doc = parsed.get(stmt_key)
            if doc is None:
                continue
            doc["pulled_at"] = datetime.utcnow()
            ops_by_coll.setdefault(coll_name, []).append(
                ReplaceOne(
                    {
                        "symbol": doc["symbol"],
                        "period_end_date": doc["period_end_date"],
                        "consolidated": doc["consolidated"],
                        "source_endpoint": ep_key,
                    },
                    doc,
                    upsert=True,
                )
            )
            parsed_counts[coll_name] = parsed_counts.get(coll_name, 0) + 1
            total_parsed += 1

    for coll_name, ops in ops_by_coll.items():
        if ops:
            await database[coll_name].bulk_write(ops, ordered=False)
            logger.info(f"Upserted {len(ops)} {coll_name} docs for {symbol}")
    _tick("db")

    for coll_name, count in parsed_counts.items():
        endpoint_breakdown[f"parsed_{coll_name}"] = count

    failures = {
        k for k, v in endpoint_breakdown.items()
        if isinstance(v, str) and v not in ("no data",)
    }
    successes = {k for k, v in endpoint_breakdown.items() if isinstance(v, int) and v > 0}
    if failures and not successes:
        pull_status = "failed"
    elif failures:
        pull_status = "partial"
    else:
        pull_status = "completed"

    if pull_status != "failed":
        now = datetime.utcnow()
        meta = await NSEStockMetadata.find_one(NSEStockMetadata.symbol == symbol)
        if meta:
            if meta.last_pull:
                meta.previous_pulls.append(meta.last_pull)
            meta.last_pull = now
            meta.updated_at = now
            await meta.save()
            logger.info(f"Updated metadata for {symbol}")
        else:
            meta = NSEStockMetadata(symbol=symbol, last_pull=now, previous_pulls=[])
            await meta.insert()
            logger.info(f"Created metadata for {symbol}")
    else:
        logger.error(f"All NSE endpoints failed for {symbol}; skipping metadata update")

    timing["total_ms"] = round((time.perf_counter() - _started) * 1000, 1)
    for key in timing["phases"]:
        timing["phases"][key] = round(timing["phases"][key], 1)

    return {
        "symbol": symbol,
        "source": "NSE",
        "status": pull_status,
        "records_pulled": total_records,
        "xbrl_parsed": total_parsed,
        "endpoint_breakdown": endpoint_breakdown,
        "timing": timing,
    }


# ---------------------------------------------------------------
# /financials — raw data & financial statement endpoints
# ---------------------------------------------------------------

def _filter_priority_fields(doc: Dict[str, Any], priority_set: set, all_fields: bool) -> Dict[str, Any]:
    if all_fields:
        return doc
    filtered = {}
    for k, v in doc.items():
        if k in priority_set or k in (
            "symbol", "period_end_date", "period_start_date", "xbrl_url",
            "broadcast_date", "consolidated", "measure", "entity_identifier",
            "fiscal_period", "filing_type", "source_endpoint", "context_ref_type", "pulled_at",
        ):
            filtered[k] = v
    return filtered


@app.get("/financials", summary="Get merged financial data (income, balance, cash flow) for a stock")
async def financials(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    consolidated: bool = Query(True),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    all_fields: bool = Query(False, description="Return all stored fields instead of only priority metrics"),
):
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")

    priority_config = _load_priority_metrics()
    all_priority = set()
    for s in priority_config.values():
        all_priority |= s

    query: Dict[str, Any] = {"symbol": symbol, "consolidated": consolidated}
    query["filing_type"] = filing_type

    database = get_database()
    merged: Dict[str, Any] = {"symbol": symbol, "consolidated": consolidated}

    for coll_name in ("income_statements", "balance_sheets", "cash_flows"):
        coll = database[coll_name]
        cursor = coll.find(query, {"_id": 0}).sort("period_end_date", -1).limit(1)
        async for doc in cursor:
            for k, v in doc.items():
                if k in ("symbol", "consolidated", "pulled_at", "_content_hash"):
                    continue
                merged[k] = v

    if len(merged) <= 2:
        raise HTTPException(status_code=404, detail=f"No financial data found for {symbol}")

    return _filter_priority_fields(merged, all_priority, all_fields)


@app.get("/financials/income-statements", summary="Fetch income statement data from DB")
async def financials_income_statements(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    consolidated: Optional[bool] = Query(True, description="Filter by consolidated (default true) or standalone (false). Pass null for both."),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    limit: int = Query(0, ge=0),
    all_fields: bool = Query(False, description="Return all stored fields instead of only priority metrics"),
):
    return await _get_statement_data("income-statements", symbol, country, source, consolidated, filing_type, limit, all_fields)


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
    return await _get_statement_data("balance-sheets", symbol, country, source, consolidated, filing_type, limit, all_fields)


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
    return await _get_statement_data("cash-flows", symbol, country, source, consolidated, filing_type, limit, all_fields)


async def _get_statement_data(
    route_name: str, symbol: str, country: str, source: str,
    consolidated: Optional[bool], filing_type: str, limit: int, all_fields: bool
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")

    coll_name = ROUTE_TO_COLLECTION.get(route_name)
    if not coll_name:
        raise HTTPException(status_code=400, detail=f"Unknown financial statement: {route_name}")

    priority_key = ROUTE_TO_PRIORITY_KEY.get(route_name)
    priority_set = _load_priority_metrics().get(priority_key, set())

    query: Dict[str, Any] = {"symbol": symbol, "filing_type": filing_type}
    if consolidated is not None:
        query["consolidated"] = consolidated

    database = get_database()
    coll = database[coll_name]
    cursor = coll.find(query, {"_id": 0}).sort("period_end_date", -1)
    if limit > 0:
        cursor = cursor.limit(limit)

    records: list = []
    async for doc in cursor:
        records.append(_filter_priority_fields(doc, priority_set, all_fields))

    response_key = priority_key
    return {response_key: records}


@app.post("/pull", summary="Pull raw stock data from exchange into DB")
async def financials_pull(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    filing_type: str = Query("quarterly", description="quarterly or annual"),
    refresh: bool = Query(False, description="Re-download and re-parse XBRL already present in the DB"),
):
    symbol = symbol.upper()
    source = source.upper()

    if filing_type not in ("quarterly", "annual"):
        raise HTTPException(status_code=400, detail="filing_type must be 'quarterly' or 'annual'")

    if country.lower() == "in" and source == "NSE":
        return await pull_nse_data(symbol, filing_type, refresh)
    raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")


@app.get("/pull", summary="Get pull status and data availability for a stock")
async def financials_pull_status(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
):
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() == "in" and source == "NSE":
        meta = await NSEStockMetadata.find_one(NSEStockMetadata.symbol == symbol)
        if not meta:
            raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

        database = get_database()
        record_counts: Dict[str, Any] = {}
        for label, coll_name in ALL_NSE_COLLECTIONS.items():
            try:
                record_counts[label] = await database[coll_name].count_documents({"symbol": symbol})
            except Exception as e:
                record_counts[label] = str(e)

        financial_breakdown: Dict[str, Any] = {}
        for coll_name in STATEMENT_COLLECTIONS.values():
            coll = database[coll_name]
            breakdown_pipeline = [
                {"$match": {"symbol": symbol}},
                {"$group": {
                    "_id": "$consolidated",
                    "count": {"$sum": 1},
                    "periods": {"$addToSet": "$period_end_date"},
                    "min_date": {"$min": "$period_end_date"},
                    "max_date": {"$max": "$period_end_date"},
                }},
            ]
            try:
                cursor = coll.aggregate(breakdown_pipeline)
                groups = await cursor.to_list(length=10)
                if groups:
                    breakdown = {}
                    for g in groups:
                        cons_label = {True: "consolidated", False: "standalone"}.get(g["_id"], "unknown")
                        breakdown[cons_label] = {
                            "count": g["count"],
                            "periods": len(g["periods"]),
                            "date_range": f"{g['min_date']} to {g['max_date']}",
                        }
                    financial_breakdown[coll_name] = breakdown
            except Exception as e:
                financial_breakdown[coll_name] = str(e)

        record_total = sum(v for v in record_counts.values() if isinstance(v, int))

        return {
            "symbol": meta.symbol,
            "source": meta.source,
            "last_pull": meta.last_pull,
            "total_pulls": len(meta.previous_pulls) + (1 if meta.last_pull else 0),
            "previous_pulls_count": len(meta.previous_pulls),
            "total_records": record_total,
            "available": record_total > 0,
            "record_counts": record_counts,
            "financial_breakdown": financial_breakdown,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")


# ---------------------------------------------------------------
# /financial-metrics — computed financial metrics
# ---------------------------------------------------------------

def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(str(v).replace(",", ""))
        return None if (f != f or abs(f) == float("inf")) else f
    except (ValueError, TypeError):
        return None


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    r = a / b
    return None if (r != r or abs(r) == float("inf")) else r


def _pct(v: Optional[float]) -> Optional[float]:
    return round(v * 100, 4) if v is not None else None


def _ttm_window(records: list, field: str, start: int = 0, require_all: bool = True) -> Optional[float]:
    vals = [_to_float(r.get(field)) for r in records[start:start + 4]]
    available = [v for v in vals if v is not None]
    if not available:
        return None
    if require_all and len(available) != 4:
        return None
    return sum(available)


def _find_record(records: list, ref_date: str, offset_months: int) -> Optional[dict]:
    try:
        ref = datetime.strptime(ref_date, "%Y-%m-%d")
        total = ref.month - offset_months
        ty = ref.year
        tm = total
        if total <= 0:
            tm = total + 12
            ty -= 1
        elif total > 12:
            tm = total - 12
            ty += 1
        for r in records:
            rd = r.get("period_end_date")
            if not rd:
                continue
            try:
                od = datetime.strptime(rd, "%Y-%m-%d")
                if od.year == ty and od.month == tm:
                    return r
            except ValueError:
                pass
    except ValueError:
        pass
    return None


@app.get("/financial-metrics", summary="Retrieve computed financial metrics for a stock")
async def financial_metrics(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
    consolidated: bool = Query(True, description="True for consolidated, False for standalone"),
    filing_type: str = Query("quarterly", description="quarterly, annual, or ttm"),
):
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")
    if filing_type not in ("quarterly", "annual", "ttm"):
        raise HTTPException(status_code=400, detail="filing_type must be 'quarterly', 'annual', or 'ttm'")

    from src.tools.nse.technicals import fetch_price_info, fetch_technicals

    database = get_database()
    is_cons = consolidated

    income_docs: dict = {}
    balance_docs: dict = {}
    cashflow_docs: dict = {}

    db_query: Dict[str, Any] = {"symbol": symbol, "consolidated": is_cons, "filing_type": "quarterly" if filing_type == "ttm" else filing_type}

    for coll_name, dest in (
        ("income_statements", income_docs),
        ("balance_sheets", balance_docs),
        ("cash_flows", cashflow_docs),
    ):
        coll = database[coll_name]
        cursor = coll.find(db_query, {"_id": 0}).sort("period_end_date", -1)
        async for doc in cursor:
            key = doc.get("period_end_date")
            if key and key not in dest:
                dest[key] = doc

    all_dates = sorted(
        set(income_docs.keys()) | set(balance_docs.keys()) | set(cashflow_docs.keys()),
        reverse=True,
    )

    merged_records: list[dict] = []
    for d in all_dates:
        merged = {"period_end_date": d, "consolidated": is_cons}
        for src in (income_docs, balance_docs, cashflow_docs):
            doc = src.get(d)
            if doc:
                for k, v in doc.items():
                    if k not in ("period_end_date", "consolidated", "symbol", "pulled_at", "_content_hash"):
                        merged[k] = v
        merged_records.append(merged)

    if not merged_records:
        return {}

    records = merged_records
    latest = records[0]

    price_info = await asyncio.to_thread(fetch_price_info, symbol, source)
    current_price = _to_float(price_info.get("current_price"))
    shares_outstanding = _to_float(price_info.get("shares_outstanding"))

    technicals = await asyncio.to_thread(fetch_technicals, symbol, source)

    # ---- extract latest (point-in-time) balance-sheet values ----
    assets_t = _to_float(latest.get("assets"))
    equity_sc = _to_float(latest.get("equity_share_capital"))
    other_eq = _to_float(latest.get("other_equity"))
    borrowings_c = _to_float(latest.get("borrowings_current"))
    borrowings_nc = _to_float(latest.get("borrowings_noncurrent"))
    ncl = _to_float(latest.get("noncurrent_liabilities"))
    cash_eq = _to_float(latest.get("cash_and_cash_equivalents"))
    debt_eq_ratio = _to_float(latest.get("debt_equity_ratio"))
    paid_up = _to_float(latest.get("paid_up_value_of_equity_share_capital"))
    face_val = _to_float(latest.get("face_value_of_equity_share_capital"))
    noncurrent_inv = _to_float(latest.get("noncurrent_investments"))
    bank_balance = _to_float(latest.get("bank_balance_other_than_cash_and_cash_equivalents"))
    ocf_ops = _to_float(latest.get("cash_flows_from_used_in_operations"))
    diluted_eps = _to_float(latest.get("diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations"))

    # ---- TTM / effective flow values ----
    is_ttm = filing_type == "ttm"
    flow_fields = [
        "revenue_from_operations", "profit_before_tax", "profit_loss_for_period",
        "finance_costs", "cash_flows_from_used_in_operating_activities",
        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations",
    ]
    ttm_values: dict = {}
    if is_ttm or filing_type == "quarterly":
        for f in flow_fields:
            ttm_values[f] = _ttm_window(
                records, f, 0,
                require_all=(f != "cash_flows_from_used_in_operating_activities"),
            )

    ttm_rev = ttm_values.get("revenue_from_operations")
    ttm_pat = ttm_values.get("profit_loss_for_period")
    ttm_pbt = ttm_values.get("profit_before_tax")
    ttm_fc = ttm_values.get("finance_costs")
    ttm_ocf = ttm_values.get("cash_flows_from_used_in_operating_activities")
    ttm_eps = ttm_values.get("basic_earnings_loss_per_share_from_continuing_and_discontinued_operations")
    ttm_ebit = (ttm_pbt or 0) + (ttm_fc or 0) if ttm_pbt is not None or ttm_fc is not None else None

    if is_ttm:
        rev, pbt, pat, fc, ocf, eps = [ttm_values[f] for f in flow_fields]
    else:
        rev = _to_float(latest.get("revenue_from_operations"))
        pbt = _to_float(latest.get("profit_before_tax"))
        pat = _to_float(latest.get("profit_loss_for_period"))
        fc = _to_float(latest.get("finance_costs"))
        ocf = _to_float(latest.get("cash_flows_from_used_in_operating_activities"))
        eps = _to_float(latest.get("basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"))
    ebit = (pbt or 0) + (fc or 0) if pbt is not None or fc is not None else None

    # ---- derive values ----
    total_debt = (borrowings_c or 0) + (borrowings_nc or 0)
    total_equity = (equity_sc or 0) + (other_eq or 0)
    market_cap = current_price * shares_outstanding if current_price is not None and shares_outstanding is not None else None
    enterprise_value = (market_cap or 0) + total_debt - (cash_eq or 0) if market_cap is not None else None

    # ---- growth rates ----
    def _growth_rate(current_val, previous_val):
        if current_val is not None and previous_val is not None and previous_val != 0:
            return _pct(_safe_div(current_val - previous_val, previous_val))
        return None

    latest_date = records[0].get("period_end_date")
    qoq_rec = _find_record(records, latest_date, 3) if latest_date else None
    yoy_rec = _find_record(records, latest_date, 12) if latest_date else None

    if is_ttm:
        rev_prior = _ttm_window(records, "revenue_from_operations", 4)
        pat_prior = _ttm_window(records, "profit_loss_for_period", 4)
        eps_prior = _ttm_window(records, "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations", 4)
        ocf_prior = _ttm_window(records, "cash_flows_from_used_in_operating_activities", 4)
        pbt_prior = _ttm_window(records, "profit_before_tax", 4)
        fc_prior = _ttm_window(records, "finance_costs", 4)
        ebit_prior = (pbt_prior or 0) + (fc_prior or 0) if pbt_prior is not None or fc_prior is not None else None

        revenue_growth = _growth_rate(ttm_rev, rev_prior)
        earnings_growth = _growth_rate(ttm_pat, pat_prior)
        eps_growth = _growth_rate(ttm_eps, eps_prior)
        ocf_growth = _growth_rate(ttm_ocf, ocf_prior)
        op_income_growth = _growth_rate(ttm_ebit, ebit_prior)
    else:
        revenue_growth = _growth_rate(
            _to_float(latest.get("revenue_from_operations")),
            _to_float(yoy_rec.get("revenue_from_operations")) if yoy_rec else None,
        )
        earnings_growth = _growth_rate(
            _to_float(latest.get("profit_loss_for_period")),
            _to_float(yoy_rec.get("profit_loss_for_period")) if yoy_rec else None,
        )
        eps_growth = _growth_rate(
            _to_float(latest.get("basic_earnings_loss_per_share_from_continuing_and_discontinued_operations")),
            _to_float(yoy_rec.get("basic_earnings_loss_per_share_from_continuing_and_discontinued_operations")) if yoy_rec else None,
        )
        ocf_growth = _growth_rate(
            _to_float(latest.get("cash_flows_from_used_in_operating_activities")),
            _to_float(yoy_rec.get("cash_flows_from_used_in_operating_activities")) if yoy_rec else None,
        )
        op_income_growth = _growth_rate(ebit, (_to_float(yoy_rec.get("profit_before_tax")) or 0) + (_to_float(yoy_rec.get("finance_costs")) or 0) if yoy_rec else None)

    book_value_growth = _growth_rate(
        total_equity if total_equity else None,
        (_to_float(yoy_rec.get("equity_share_capital")) or 0) + (_to_float(yoy_rec.get("other_equity")) or 0) if yoy_rec else None,
    )
    ebitda_growth = op_income_growth  # placeholder — same as operating income growth if no depreciation data

    # ---- CAGR ----
    if is_ttm:
        eps_vals = [_ttm_window(records, "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations", i) for i in range(max(0, len(records) - 3))]
    else:
        eps_vals = [_to_float(r.get("basic_earnings_loss_per_share_from_continuing_and_discontinued_operations")) for r in records]
    cagr = None
    if len(eps_vals) >= 13 and eps_vals[0] is not None and eps_vals[12] is not None and eps_vals[0] > 0 and eps_vals[12] > 0:
        cagr = ((eps_vals[0] / eps_vals[12]) ** (1.0 / 3) - 1) * 100

    peg_growth = cagr if cagr is not None and cagr > 0 else eps_growth

    # --- Technicals ---
    result: Dict[str, Any] = {
        "symbol": symbol,
        "period_end_date": latest.get("period_end_date"),
        "consolidated": is_cons,
        "filing_type": filing_type,
    }
    for k in ("current_price", "rsi_14", "sma_20", "sma_50", "sma_200", "ema_20", "bb_upper", "bb_middle", "bb_lower",
              "atr_14", "volume", "avg_volume_10d", "avg_volume_3m", "high_52w", "low_52w", "change_pct",
              "volume_ratio", "delivery_percentage", "relative_strength"):
        if k in technicals:
            result[k] = technicals[k]
    # override when price_info gives a better price
    if current_price is not None:
        result["current_price"] = current_price

    # --- Valuation ---
    result["enterprise_value"] = enterprise_value
    val_eps = ttm_eps if ttm_eps is not None else eps
    val_rev = ttm_rev if ttm_rev is not None else rev
    val_ebit = ttm_ebit if ttm_ebit is not None else ebit
    val_ocf = ttm_ocf if ttm_ocf is not None else ocf
    result["price_to_earnings_ratio"] = _safe_div(current_price, val_eps) if current_price is not None and val_eps is not None and val_eps != 0 else None
    pe = result["price_to_earnings_ratio"]
    bvps = _safe_div(total_equity, shares_outstanding) if total_equity and shares_outstanding else None
    result["price_to_book_ratio"] = _safe_div(current_price, bvps) if current_price else None
    sps = _safe_div(val_rev, shares_outstanding) if shares_outstanding else None
    result["price_to_sales_ratio"] = _safe_div(current_price, sps) if current_price else None
    result["enterprise_value_to_ebitda_ratio"] = _safe_div(enterprise_value, val_ebit) if enterprise_value is not None else None
    result["enterprise_value_to_revenue_ratio"] = _safe_div(enterprise_value, val_rev) if enterprise_value is not None else None
    fcf = val_ocf or 0  # no capex data, so approximate with OCF
    result["free_cash_flow_yield"] = _pct(_safe_div(fcf, market_cap)) if market_cap else None
    result["peg_ratio"] = _safe_div(pe, peg_growth) if pe is not None and peg_growth is not None and peg_growth > 0 else None

    # --- Profitability ---
    result["gross_margin"] = None  # need COGS
    result["operating_margin"] = _pct(_safe_div(ebit, rev)) if rev else None
    result["net_margin"] = _pct(_safe_div(pat, rev)) if rev else None
    result["return_on_equity"] = _pct(_safe_div(pat, total_equity)) if total_equity else None
    result["return_on_assets"] = _pct(_safe_div(pat, assets_t)) if assets_t else None
    result["return_on_invested_capital"] = _pct(_safe_div(ebit, (assets_t or 0) - (ncl or 0))) if ebit is not None and assets_t is not None else None

    # --- Efficiency ---
    result["asset_turnover"] = _safe_div(rev, assets_t) if assets_t else None
    result["inventory_turnover"] = None  # need COGS and inventory
    result["receivables_turnover"] = None  # need trade_receivables
    result["days_sales_outstanding"] = None
    result["operating_cycle"] = None
    result["working_capital_turnover"] = None

    # --- Liquidity ---
    result["current_ratio"] = None  # need current_assets and current_liabilities
    result["quick_ratio"] = None
    result["cash_ratio"] = _safe_div(cash_eq, borrowings_c) if borrowings_c else None
    result["operating_cash_flow_ratio"] = _safe_div(ocf, borrowings_c) if borrowings_c else None

    # --- Solvency ---
    result["debt_to_equity"] = debt_eq_ratio if debt_eq_ratio is not None else _safe_div(total_debt, total_equity)
    result["debt_to_assets"] = _safe_div(total_debt, assets_t) if assets_t else None
    result["interest_coverage"] = _safe_div(ebit, fc) if fc else None

    # --- Growth ---
    result["revenue_growth"] = revenue_growth
    result["earnings_growth"] = earnings_growth
    result["book_value_growth"] = book_value_growth
    result["earnings_per_share_growth"] = eps_growth
    result["free_cash_flow_growth"] = ocf_growth
    result["operating_income_growth"] = op_income_growth
    result["ebitda_growth"] = ebitda_growth

    # --- Per Share ---
    result["earnings_per_share"] = eps
    result["book_value_per_share"] = bvps
    result["free_cash_flow_per_share"] = _safe_div(fcf, shares_outstanding) if shares_outstanding else None

    # --- Other ---
    result["payout_ratio"] = None  # need dividends
    result["market_capitalization"] = market_cap
    result["total_debt"] = total_debt if total_debt else None
    result["total_equity"] = total_equity if total_equity else None
    result["cash_and_equivalents"] = cash_eq

    return result


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
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")

    ep_key = f"announcements-{market}"
    if ep_key not in ENDPOINTS:
        raise HTTPException(status_code=400, detail=f"Invalid market '{market}'. Use 'equities' or 'sme'.")

    try:
        url = ENDPOINTS[ep_key].format(symbol=symbol)
        data = await asyncio.to_thread(
            lambda: nse_scraper.api._safe_json(nse_scraper.api._call(url, symbol=symbol))
        )
        if not isinstance(data, list):
            return {"symbol": symbol, "source": source, "market": market, "announcements": []}

        cleaned = []
        for a in data:
            cleaned.append({
                "date": a.get("an_dt"),
                "heading": a.get("attchmntText"),
                "category": a.get("desc"),
                "attachment": a.get("attchmntFile"),
                "attachment_size": a.get("attFileSize"),
                "has_xbrl": a.get("hasXbrl", False),
            })
        return {"symbol": symbol, "source": source, "market": market, "announcements": cleaned}
    except CookieError as e:
        logger.error(f"Cookie failure fetching announcements for {symbol}: {e}")
        raise HTTPException(status_code=503, detail="NSE session unavailable (cookie failure)")
    except Exception as e:
        logger.error(f"Error fetching announcements for {symbol}: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------
# /shareholdings — dedicated endpoint
# ---------------------------------------------------------------

@app.get("/shareholdings", summary="Fetch shareholding pattern for a stock (parsed from XBRL)")
async def shareholdings(
    symbol: str,
    country: str = Query("in"),
    source: str = Query("nse"),
):
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")

    database = get_database()
    coll = database["shareholdings"]

    existing = await coll.find_one(
        {"symbol": symbol, "filing_type": "shareholding"},
        {"_id": 0},
        sort=[("period_end_date", -1)],
    )
    if existing:
        priority = _load_priority_metrics().get("shareholdings", set())
        return {"symbol": symbol, "source": source, "shareholdings": _filter_priority_fields(existing, priority, False)}

    try:
        records = await asyncio.to_thread(
            lambda: nse_scraper.api.shareholding_xbrls(symbol)
        )
        if not isinstance(records, list) or not records:
            raise HTTPException(status_code=404, detail=f"No shareholding data found for {symbol}")

        for record in records:
            parsed = await asyncio.to_thread(nse_scraper.process_xbrl, record, symbol, "shareholding-pattern")
            if parsed is None or parsed.get("shareholding") is None:
                continue
            doc = parsed["shareholding"]
            doc["pulled_at"] = datetime.utcnow()
            result = await coll.replace_one(
                {
                    "symbol": doc["symbol"],
                    "period_end_date": doc["period_end_date"],
                    "consolidated": doc["consolidated"],
                    "source_endpoint": "shareholding-pattern",
                },
                doc,
                upsert=True,
            )
            logger.info(f"Saved shareholding for {symbol} - {doc['period_end_date']}")
            priority = _load_priority_metrics().get("shareholdings", set())
            return {"symbol": symbol, "source": source, "shareholdings": _filter_priority_fields(doc, priority, False)}

        raise HTTPException(status_code=404, detail=f"No parseable shareholding XBRL found for {symbol}")
    except CookieError as e:
        logger.error(f"Cookie failure fetching shareholdings for {symbol}: {e}")
        raise HTTPException(status_code=503, detail="NSE session unavailable (cookie failure)")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching shareholdings for {symbol}: {e}")
        raise HTTPException(status_code=502, detail=str(e))


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
    return {"status": "not_implemented", "note": "Macroeconomic data not yet implemented"}


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

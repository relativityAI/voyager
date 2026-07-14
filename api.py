import asyncio
import csv
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from __version__ import __version__
from src.db.connection import get_database, init_db
from src.db.models import NSEStockMetadata
from src.tools.nse.client import ENDPOINTS, NSEIndia

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beanie via root /db...")
    await init_db()
    yield


app = FastAPI(title="Voyager", version=__version__, lifespan=lifespan)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "src", "assets")
WEB_SOURCES_CSV = os.path.join(ASSETS_DIR, "web_sources.csv")
SOURCES_CSV = os.path.join(ASSETS_DIR, "sources.csv")
COUNTRIES_CSV = os.path.join(ASSETS_DIR, "countries.csv")


def _load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: v.strip() for k, v in row.items()})
    return rows


def load_web_sources(source: Optional[str] = None) -> list[dict]:
    sources = []
    with open(WEB_SOURCES_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if source and row["source"].strip().upper() != source.upper():
                continue
            sources.append({
                "id": row["id"].strip(),
                "name": row["name"].strip(),
                "type": row["type"].strip(),
            })
    return sources


@app.get("/", summary="Health check")
def ping():
    """Returns ok=1 if the server is alive."""
    return {"ok": 1}


@app.get("/equity/sources", summary="List available data sources")
def equity_sources():
    """Returns a list of available data sources (e.g. NSE, SEC) with their IDs and names."""
    return {"sources": _load_csv(SOURCES_CSV)}


@app.get("/equity/countries", summary="List supported countries")
def equity_countries():
    """Returns a list of supported countries with their codes and names."""
    return {"countries": _load_csv(COUNTRIES_CSV)}


# --- NSE Stock Data Pull ---

class PullStockDataRequest(BaseModel):
    source: str = "NSE"
    symbol: str


nse_scraper = NSEIndia()

NSE_RAW_COLLECTIONS = {key: f"nse_{key.replace('-', '_')}" for key in ENDPOINTS}

NSE_PARSED_COLLECTIONS: Dict[str, str] = {
    "quarterly-financials": "nse_quarterly_financials",
    "annual-financials": "nse_annual_financials",
    "shareholding-financials": "nse_shareholding_financials",
}

ALL_NSE_COLLECTIONS: Dict[str, str] = {**NSE_RAW_COLLECTIONS, **NSE_PARSED_COLLECTIONS}

XBRL_PARSE_MAP: Dict[str, str] = {
    "integrated-filing": "nse_quarterly_financials",
    "quarterly-results": "nse_quarterly_financials",
    "annual-results": "nse_annual_financials",
    "shareholding-pattern": "nse_shareholding_financials",
}


async def pull_nse_data(symbol: str) -> Dict[str, Any]:
    database = get_database()
    total_records = 0
    total_parsed = 0
    endpoint_breakdown: Dict[str, Any] = {}
    raw_by_endpoint: Dict[str, list] = {}

    # Phase 1: fetch and save raw endpoint data
    for endpoint_key, endpoint_url in ENDPOINTS.items():
        collection = database[NSE_RAW_COLLECTIONS[endpoint_key]]
        count = 0
        endpoint_records: list = []

        try:
            url = endpoint_url.format(symbol=symbol) if "{symbol}" in endpoint_url else endpoint_url
            data = await asyncio.to_thread(
                lambda: nse_scraper.api._safe_json(nse_scraper.api._call(url, symbol=symbol))
            )

            if isinstance(data, dict):
                inner = data.get("data")
                records = inner if isinstance(inner, list) else [data]
            elif isinstance(data, list):
                records = data
            else:
                records = []

            for record in records:
                if not isinstance(record, dict):
                    continue
                count += 1
                record["symbol"] = symbol
                record["pulled_at"] = datetime.utcnow()

                stable = {k: v for k, v in record.items() if k not in ("_content_hash", "pulled_at")}
                content_hash = hashlib.md5(
                    json.dumps(stable, sort_keys=True, default=str).encode()
                ).hexdigest()
                record["_content_hash"] = content_hash

                await collection.replace_one({"_content_hash": content_hash}, record, upsert=True)
                endpoint_records.append(record)

            total_records += count
            endpoint_breakdown[endpoint_key] = count
            raw_by_endpoint[endpoint_key] = endpoint_records
            logger.info(f"Pulled {count} records for {endpoint_key} ({symbol})")

        except Exception as e:
            logger.error(f"Error pulling {endpoint_key} for {symbol}: {e}")
            endpoint_breakdown[endpoint_key] = str(e)
            raw_by_endpoint[endpoint_key] = []

    # Phase 2: parse XBRL XML files for integrated-filing, quarterly-results, annual-results
    parsed_by_target: Dict[str, int] = {}

    for ep_key, target_coll_name in XBRL_PARSE_MAP.items():
        records = raw_by_endpoint.get(ep_key, [])
        if not isinstance(records, list) or not records:
            logger.info(f"No records to parse for {ep_key} ({symbol})")
            continue

        if ep_key == "integrated-filing":
            records = sorted(records, key=lambda r: 1 if r.get("type_Sub") == "Revision" else 0)

        target_coll = database[target_coll_name]
        parsed_count = 0

        for record in records:
            try:
                parsed = await asyncio.to_thread(nse_scraper.process_xbrl, record, symbol, ep_key)
                if parsed is None:
                    logger.debug(f"Skipped {ep_key} record for {symbol} (no parseable XML)")
                    continue

                existing = await target_coll.find_one({
                    "symbol": parsed["symbol"],
                    "date": parsed["date"],
                    "consolidated": parsed["consolidated"],
                })

                if existing:
                    await target_coll.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {
                            "financials": parsed["financials"],
                            "broadcast_date": parsed.get("broadcast_date"),
                            "source_endpoint": ep_key,
                            "pulled_at": datetime.utcnow(),
                        }},
                    )
                    logger.debug(f"Updated parsed {ep_key} for {symbol} - {parsed['date']} ({parsed['consolidated']})")
                else:
                    parsed["source_endpoint"] = ep_key
                    parsed["pulled_at"] = datetime.utcnow()
                    await target_coll.insert_one(parsed)
                    logger.debug(f"Inserted parsed {ep_key} for {symbol} - {parsed['date']} ({parsed['consolidated']})")

                parsed_count += 1

            except Exception as e:
                logger.error(f"Error parsing XBRL for {symbol} {ep_key}: {e}")

        total_parsed += parsed_count
        parsed_by_target[target_coll_name] = parsed_count
        logger.info(f"Parsed {parsed_count} XBRL from {ep_key} -> {target_coll_name} ({symbol})")

    for coll_name, count in parsed_by_target.items():
        label = coll_name.replace("nse_", "")
        endpoint_breakdown[label] = count

    # Phase 3: update metadata
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

    return {
        "symbol": symbol,
        "source": "NSE",
        "status": "completed",
        "records_pulled": total_records,
        "xbrl_parsed": total_parsed,
        "endpoint_breakdown": endpoint_breakdown,
    }


# ============================================================
# Equity Endpoints
# ============================================================

AVAILABLE_DATA_TYPES = list(ENDPOINTS.keys())


@app.get("/equity/data", summary="Fetch specific raw data types for a stock from the exchange")
async def equity_data(
    symbol: str,
    types: list[str] = Query(..., description=f"Data types to fetch. Available: {AVAILABLE_DATA_TYPES}"),
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Fetch raw data directly from the exchange API for a stock. Specify which data types you want (e.g. 'announcements-equity', 'quarterly-results'). Returns the raw JSON response for each requested type."""
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")

    invalid = [t for t in types if t not in ENDPOINTS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid data types: {invalid}. Available: {AVAILABLE_DATA_TYPES}")

    results: Dict[str, Any] = {}
    for dtype in types:
        try:
            url = ENDPOINTS[dtype].format(symbol=symbol) if "{symbol}" in ENDPOINTS[dtype] else ENDPOINTS[dtype]
            data = await asyncio.to_thread(
                lambda u=url: nse_scraper.api._safe_json(nse_scraper.api._call(u, symbol=symbol))
            )
            results[dtype] = data
        except Exception as e:
            logger.error(f"Error fetching {dtype} for {symbol}: {e}")
            results[dtype] = {"error": str(e)}

    return {
        "symbol": symbol,
        "source": source,
        "types_requested": types,
        "data": results,
    }


@app.post("/equity/data/pull", summary="Pull raw stock data from exchange into DB")
async def equity_data_pull(
    symbol: str,
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Pull raw stock data from the specified exchange, parse XBRL filings, and store in MongoDB. Returns record counts and parse summary."""
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() == "in" and source == "NSE":
        return await pull_nse_data(symbol)
    raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")


@app.get("/equity/data/status", summary="Get pull status and data availability for a stock")
async def equity_data_status(
    symbol: str,
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Returns last pull time, record counts per collection, and financial breakdowns for a stock."""
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

        # Detailed breakdown for parsed financial collections
        financial_breakdown: Dict[str, Any] = {}
        for coll_name in ("nse_quarterly_financials", "nse_annual_financials", "nse_shareholding_financials"):
            coll = database[coll_name]
            breakdown_pipeline = [
                {"$match": {"symbol": symbol}},
                {"$group": {
                    "_id": "$consolidated",
                    "count": {"$sum": 1},
                    "quarters": {"$addToSet": "$date"},
                    "min_date": {"$min": "$date"},
                    "max_date": {"$max": "$date"},
                }},
            ]
            try:
                cursor = coll.aggregate(breakdown_pipeline)
                groups = await cursor.to_list(length=10)
                if groups:
                    breakdown = {}
                    for g in groups:
                        cons_type = g["_id"] or "unknown"
                        breakdown[cons_type] = {
                            "count": g["count"],
                            "quarters": len(g["quarters"]),
                            "date_range": f"{g['min_date']} to {g['max_date']}",
                        }
                    financial_breakdown[coll_name] = breakdown
            except Exception as e:
                financial_breakdown[coll_name] = str(e)

        return {
            "symbol": meta.symbol,
            "source": meta.source,
            "last_pull": meta.last_pull,
            "total_pulls": len(meta.previous_pulls) + (1 if meta.last_pull else 0),
            "previous_pulls": meta.previous_pulls,
            "record_counts": record_counts,
            "financial_breakdown": financial_breakdown,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")


@app.get("/equity/data/metrics", summary="Retrieve raw/parsed financial metrics from DB")
async def equity_data_metrics(
    symbol: str,
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
    collections: list[str] = Query(None),
    metrics: list[str] = Query(None),
    limit: int = Query(0, ge=0),
):
    """Fetch raw endpoint data and parsed financial records from MongoDB, with optional filtering by collection and metric tags."""
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() == "in" and source == "NSE":
        database = get_database()
        result: Dict[str, Any] = {}
        total = 0

        target = {k: v for k, v in ALL_NSE_COLLECTIONS.items() if not collections or k in collections}
        parsed_colls = set(NSE_PARSED_COLLECTIONS.values())

        for label, coll_name in target.items():
            coll = database[coll_name]
            try:
                cursor = coll.find({"symbol": symbol}, {"_id": 0}).sort("pulled_at", -1)
                if limit > 0:
                    cursor = cursor.limit(limit)
                records = await cursor.to_list(length=limit if limit > 0 else 10000)

                if metrics and coll_name in parsed_colls:
                    metrics_set = set(metrics)
                    filtered = []
                    for rec in records:
                        fin = rec.get("financials", [])
                        rec["financials"] = [f for f in fin if f.get("tag") in metrics_set]
                        if rec["financials"]:
                            filtered.append(rec)
                    records = filtered

                result[label] = records
                total += len(records)
            except Exception as e:
                result[label] = str(e)

        return {
            "symbol": symbol,
            "source": source,
            "collections_requested": collections or list(ALL_NSE_COLLECTIONS.keys()),
            "total_records": total,
            "data": result,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")


@app.get("/equity/data/metrics/available", summary="List all available financial metrics, valuation, and technical indicators")
async def equity_data_metrics_available(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Returns the full catalog of available financial metrics including raw fields, valuation ratios, and technical indicators, organized by category."""
    from src.tools.nse.ratios import get_metrics_catalog
    from src.tools.nse.technicals import get_technicals_catalog
    from src.tools.nse.valuation import get_valuation_catalog

    categories = get_metrics_catalog()
    categories.append(get_valuation_catalog())
    categories.append(get_technicals_catalog())

    return {
        "country": country,
        "source": source.upper(),
        "categories": categories,
    }


@app.get("/equity/data/ratios/available", summary="List available ratio categories and their definitions")
async def equity_data_ratios_available(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Returns all available financial ratio categories (profitability, return, capital structure, liquidity, cash flow, earnings quality, growth) with their individual ratio definitions."""
    from src.tools.nse.ratios import RATIO_CATEGORIES, Growth

    ratios_by_category = []
    for cat in RATIO_CATEGORIES:
        ratios_by_category.append({
            "id": cat["id"],
            "name": cat["name"],
            "type": cat["type"],
        })
    ratios_by_category.append({
        "id": "growth",
        "name": "Growth Metrics",
        "type": "ratio",
        "metrics": [{"id": r["id"], "name": r["name"]} for r in Growth],
    })

    return {
        "country": country,
        "source": source.upper(),
        "categories": ratios_by_category,
    }


@app.get("/equity/data/ratios", summary="Compute financial ratios, growth, valuation, and technicals for a stock")
async def equity_data_ratios(
    symbol: str,
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
    consolidated: str = Query("Consolidated", description="Use 'Consolidated' or 'Standalone' financials"),
):
    """Computes static financial ratios (profitability, leverage, liquidity), QoQ/YoY growth, valuation multiples (P/E, P/B, PEG), and technical indicators for a stock."""
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() == "in" and source == "NSE":
        from src.tools.nse.ratios import (
            compute_growth,
            compute_static,
            extract_quarterly_value,
            flatten_financials,
        )
        from src.tools.nse.technicals import fetch_price_info, fetch_technicals
        from src.tools.nse.valuation import compute_valuation, to_float

        database = get_database()
        records: list = []

        for coll_name in ("nse_quarterly_financials", "nse_annual_financials"):
            coll = database[coll_name]
            cursor = coll.find(
                {"symbol": symbol, "consolidated": consolidated},
                {"_id": 0},
            ).sort("date", -1)
            async for doc in cursor:
                records.append(doc)

        if not records:
            raise HTTPException(status_code=404, detail=f"No financial data found for {symbol} ({consolidated})")

        # Deduplicate by date: quarterly and annual collections may both have records for the same date
        seen_dates: set = set()
        deduped: list = []
        for rec in records:
            key = (rec.get("date"), rec.get("consolidated"))
            if key not in seen_dates:
                seen_dates.add(key)
                deduped.append(rec)
        records = deduped

        price_info = await asyncio.to_thread(fetch_price_info, symbol, source)
        current_price = price_info.get("current_price")
        shares_outstanding = price_info.get("shares_outstanding")

        # Pre-compute quarterly lists for P&L fields (used for TTM ratios)
        PNL_FIELDS = [
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "RevenueFromOperations", "ProfitLossForPeriod", "ProfitBeforeTax",
            "FinanceCosts", "CashFlowsFromUsedInOperatingActivities",
        ]
        qtrly: dict[str, list] = {f: [] for f in PNL_FIELDS}
        for rec in records:
            fin = rec.get("financials", [])
            for f in PNL_FIELDS:
                qtrly[f].append(to_float(extract_quarterly_value(fin, f)))

        def _ttm_sum(values: list) -> Optional[float]:
            if len(values) >= 4:
                vals = [values[j] for j in range(4)]
                if all(v is not None for v in vals):
                    return sum(vals)
            return None

        ttm_eps = _ttm_sum(qtrly["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"])
        ttm_revenue = _ttm_sum(qtrly["RevenueFromOperations"])

        def _ttm_data(data: dict, i: int) -> dict:
            """Replace single-period P&L values with TTM sums for ratio computation."""
            d = dict(data)
            for f in ("ProfitLossForPeriod", "RevenueFromOperations", "ProfitBeforeTax", "FinanceCosts", "CashFlowsFromUsedInOperatingActivities"):
                if f in qtrly:
                    ttm = _ttm_sum(qtrly[f][i:])
                    if ttm is not None:
                        d[f] = str(ttm)
            return d

        # Valuation uses latest record's financials + today's price
        latest_data = flatten_financials(records[0].get("financials", [])) if records else {}
        latest_fin = records[0].get("financials", []) if records else []
        latest_eps = to_float(extract_quarterly_value(latest_fin, "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"))

        def _find_financials(ref_date: str, offset_months: int) -> Optional[list]:
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
                    rd = r.get("date")
                    if not rd:
                        continue
                    try:
                        od = datetime.strptime(rd, "%Y-%m-%d")
                        if od.year == ty and od.month == tm:
                            return r.get("financials", [])
                    except ValueError:
                        pass
            except ValueError:
                pass
            return None

        # EPS growth for PEG: prefer YoY, fall back to QoQ
        eps_qoq = None
        eps_yoy = None
        latest_date = records[0].get("date") if records else None
        if latest_date:
            qoq_fin = _find_financials(latest_date, 3)
            if qoq_fin is not None:
                qoq_g = compute_growth(latest_data, flatten_financials(qoq_fin))
                eps_qoq = qoq_g.get("eps_growth")
            yoy_fin = _find_financials(latest_date, 12)
            if yoy_fin is not None:
                yoy_g = compute_growth(latest_data, flatten_financials(yoy_fin))
                eps_yoy = yoy_g.get("eps_growth")

        # 3-year CAGR for PEG
        cagr = None
        eps_vals = qtrly["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"]
        if len(eps_vals) >= 13:
            now = eps_vals[0]
            old = eps_vals[12]
            if now is not None and old is not None and now > 0 and old > 0:
                cagr = ((now / old) ** (1.0 / 3) - 1) * 100

        peg_growth = cagr if cagr is not None else (eps_yoy or eps_qoq)

        valuation = compute_valuation(latest_data, current_price, shares_outstanding, peg_growth, ttm_eps, latest_eps, ttm_revenue)

        result: list = []
        for i, rec in enumerate(records):
            data = flatten_financials(rec.get("financials", []))
            rdata = _ttm_data(data, i)
            static = compute_static(rdata)
            growth: dict = {}
            rec_date = rec.get("date")

            if rec_date:
                qoq_fin = _find_financials(rec_date, 3)
                if qoq_fin is not None:
                    qoq = compute_growth(data, flatten_financials(qoq_fin))
                    for k, v in qoq.items():
                        growth[f"{k}_qoq"] = v

                yoy_fin = _find_financials(rec_date, 12)
                if yoy_fin is not None:
                    yoy = compute_growth(data, flatten_financials(yoy_fin))
                    for k, v in yoy.items():
                        growth[f"{k}_yoy"] = v

            entry = {
                "date": rec.get("date"),
                "consolidated": rec.get("consolidated"),
                "source_endpoint": rec.get("source_endpoint"),
                "broadcast_date": rec.get("broadcast_date"),
                "ratios": static,
            }

            if growth:
                entry["ratios"]["growth"] = growth

            result.append(entry)

        technicals = await asyncio.to_thread(fetch_technicals, symbol, source)

        return {
            "symbol": symbol,
            "source": source,
            "consolidated": consolidated,
            "current_price": current_price,
            "valuation": valuation,
            "records": result,
            "technicals": technicals,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' for country '{country}' is not yet supported")


@app.get("/equity/read-pdf", summary="Fetch and extract text from an exchange PDF document")
def equity_read_pdf(
    url: str,
    symbol: str = None,
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
):
    """Fetches a PDF document from the exchange and extracts its text content. Returns the full text of the document."""
    content = nse_scraper.read_nse_document(url, symbol=symbol)
    if content is None:
        raise HTTPException(status_code=502, detail="Failed to fetch or parse PDF from exchange")
    return {"url": url, "content": content}


@app.get("/equity/industries", summary="List available industries for a country/source")
async def equity_industries(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Returns a list of available industry classifications for the specified country and data source."""
    if country.lower() == "in" and source.upper() == "NSE":
        return {
            "country": country,
            "source": source.upper(),
            "industries": [],
            "note": "Industry data not yet implemented for NSE",
        }
    raise HTTPException(status_code=501, detail=f"Industries not available for country '{country}', source '{source}'")


@app.get("/equity/sectors", summary="List available sectors for a country/source")
async def equity_sectors(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Returns a list of available sector classifications for the specified country and data source."""
    if country.lower() == "in" and source.upper() == "NSE":
        return {
            "country": country,
            "source": source.upper(),
            "sectors": [],
            "note": "Sector data not yet implemented for NSE",
        }
    raise HTTPException(status_code=501, detail=f"Sectors not available for country '{country}', source '{source}'")


@app.get("/equity/indices", summary="List available market indices for a country/source")
async def equity_indices(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Returns a list of available market indices (NIFTY 50, SENSEX, etc.) for the specified country and data source."""
    if country.lower() == "in" and source.upper() == "NSE":
        return {
            "country": country,
            "source": source.upper(),
            "indices": [],
            "note": "Index data not yet implemented for NSE",
        }
    raise HTTPException(status_code=501, detail=f"Indices not available for country '{country}', source '{source}'")


@app.get("/equity/web/sources", summary="List available web screening sources")
def equity_web_sources(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: Optional[str] = Query(None, description="Filter by data source (e.g. 'NSE')"),
):
    """Returns a list of available web sources (Screener.in, Trendlyne, etc.) with their IDs, names, and types."""
    return {"country": country, "sources": load_web_sources(source)}


@app.get("/equity/web/data", summary="Fetch live content from a named web source")
def equity_web_data(
    id: str,
    symbol: str,
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
    source: str = Query("nse", description="Data source: 'nse' for NSE India"),
):
    """Looks up a web source by ID, constructs the URL with the stock symbol, fetches the page, and returns the HTML content."""
    with open(WEB_SOURCES_CSV, newline="") as f:
        reader = csv.DictReader(f)
        url_format = None
        for row in reader:
            if row["id"].strip() == id and row["source"].strip().upper() == source.upper():
                url_format = row["url_format"].strip()
                break

    if not url_format:
        raise HTTPException(status_code=404, detail=f"Web source '{id}' not found for source '{source}'")

    url = url_format.format(symbol=symbol.upper())
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return {
            "id": id,
            "country": country,
            "source": source.upper(),
            "symbol": symbol.upper(),
            "url": url,
            "content": resp.text,
        }
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch {url}: {e}")


# ============================================================
# Fund Endpoints (Dummy)
# ============================================================


@app.get("/fund/in/available", summary="List available mutual fund categories in India")
def fund_in_available():
    """Returns a list of available mutual fund categories and sub-categories in India (equity, debt, hybrid, etc.)."""
    return {
        "country": "in",
        "categories": [],
        "note": "Mutual fund data not yet implemented",
    }


@app.get("/fund/in/data", summary="Fetch mutual fund data for a given scheme")
def fund_in_data(
    scheme: str = Query(..., description="Mutual fund scheme name or code"),
):
    """Fetches detailed data for a specific mutual fund scheme including NAV, holdings, performance, and expense ratio."""
    return {
        "country": "in",
        "scheme": scheme,
        "data": None,
        "note": "Mutual fund data not yet implemented",
    }


# ============================================================
# Macro Endpoints (Dummy)
# ============================================================


@app.get("/macro/available", summary="List available macroeconomic indicators")
def macro_available(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
):
    """Returns a list of available macroeconomic indicators (GDP, inflation, interest rates, etc.) for the specified country."""
    return {
        "country": country,
        "indicators": [],
        "note": "Macro data not yet implemented",
    }


@app.get("/macro/data", summary="Fetch macroeconomic data for a country/indicator")
def macro_data(
    indicator: str = Query(..., description="Macro indicator name (e.g. 'gdp', 'inflation')"),
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
):
    """Fetches time-series macroeconomic data for the specified indicator and country."""
    return {
        "country": country,
        "indicator": indicator,
        "data": None,
        "note": "Macro data not yet implemented",
    }


# ============================================================
# News Endpoints (Dummy)
# ============================================================


@app.get("/news/available", summary="List available news sources and categories")
def news_available(
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
):
    """Returns a list of available news sources and categories (business, markets, economy, etc.) for the specified country."""
    return {
        "country": country,
        "sources": [],
        "note": "News data not yet implemented",
    }


@app.get("/news/data", summary="Fetch news articles for a stock or topic")
def news_data(
    query: str = Query(..., description="Search query (stock symbol, company name, or topic)"),
    country: str = Query("in", description="Country code: 'in' for India, 'us' for USA"),
):
    """Fetches recent news articles matching the query from available news sources for the specified country."""
    return {
        "country": country,
        "query": query,
        "articles": [],
        "note": "News data not yet implemented",
    }


if __name__ == "__main__":
    uvicorn.run(
        "api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True
    )

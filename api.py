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
from src.tools.exchange.nse import ENDPOINTS, NSEIndia

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beanie via root /db...")
    await init_db()
    yield


app = FastAPI(title="Voyager", version=__version__, lifespan=lifespan)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "src", "assets")
WEB_SOURCES_CSV = os.path.join(ASSETS_DIR, "web_sources.csv")


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


@app.get("/")
def ping():
    return {"ok": 1}


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


@app.post("/pull-stock-data")
async def pull_stock_data(request: PullStockDataRequest):
    source = request.source.upper()
    symbol = request.symbol.upper()

    if source == "NSE":
        return await pull_nse_data(symbol)
    raise HTTPException(status_code=501, detail=f"Source '{source}' is not yet supported")


@app.get("/stock-data-status")
async def stock_data_status(symbol: str, source: str = "NSE"):
    symbol = symbol.upper()
    source = source.upper()

    if source == "NSE":
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
        available_metrics: Dict[str, Any] = {}
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

            # Unique metric tags available in this collection for this symbol
            try:
                tag_pipeline = [
                    {"$match": {"symbol": symbol}},
                    {"$unwind": "$financials"},
                    {"$group": {"_id": "$financials.tag"}},
                    {"$sort": {"_id": 1}},
                ]
                tag_cursor = coll.aggregate(tag_pipeline)
                tags = await tag_cursor.to_list(length=200)
                available_metrics[coll_name] = [t["_id"] for t in tags if t["_id"]]
            except Exception as e:
                available_metrics[coll_name] = str(e)

        # Static metric catalog from the financial fields definition
        from src.ratios.nse import FINANCIAL_FIELD_MAP
        metrics_catalog = [
            {"id": f["id"], "name": f["name"], "type": f["type"], "category": f["category"]}
            for f in FINANCIAL_FIELD_MAP.values()
        ]

        return {
            "symbol": meta.symbol,
            "source": meta.source,
            "last_pull": meta.last_pull,
            "total_pulls": len(meta.previous_pulls) + (1 if meta.last_pull else 0),
            "previous_pulls": meta.previous_pulls,
            "record_counts": record_counts,
            "financial_breakdown": financial_breakdown,
            "available_metrics": available_metrics,
            "metrics_catalog": metrics_catalog,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' is not yet supported")


@app.get("/stock-data")
async def get_stock_data(
    symbol: str,
    source: str = "NSE",
    collections: list[str] = Query(None),
    metrics: list[str] = Query(None),
    limit: int = Query(0, ge=0),
):
    symbol = symbol.upper()
    source = source.upper()

    if source == "NSE":
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

    raise HTTPException(status_code=501, detail=f"Source '{source}' is not yet supported")


@app.get("/available-metrics")
async def available_metrics(source: str = "NSE"):
    from src.ratios.nse import get_metrics_catalog
    from src.ratios.technicals import get_technicals_catalog
    from src.ratios.valuation import get_valuation_catalog

    categories = get_metrics_catalog()
    categories.append(get_valuation_catalog())
    categories.append(get_technicals_catalog())

    return {
        "source": source.upper(),
        "categories": categories,
    }


@app.get("/financial-ratios")
async def financial_ratios(symbol: str, source: str = "NSE", consolidated: str = "Consolidated"):
    symbol = symbol.upper()
    source = source.upper()

    if source == "NSE":
        from src.ratios.nse import (
            compute_growth,
            compute_static,
            extract_quarterly_value,
            flatten_financials,
        )
        from src.ratios.technicals import fetch_price_info, fetch_technicals
        from src.ratios.valuation import compute_valuation, to_float

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

        price_info = await asyncio.to_thread(fetch_price_info, symbol, source)
        current_price = price_info.get("current_price")
        shares_outstanding = price_info.get("shares_outstanding")

        # Pre-compute EPS and Revenue for TTM and CAGR (quarterly context only)
        eps_list = []
        rev_list = []
        for rec in records:
            fin = rec.get("financials", [])
            eps_list.append(to_float(extract_quarterly_value(fin, "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations")))
            rev_list.append(to_float(extract_quarterly_value(fin, "RevenueFromOperations")))

        # Valuation uses latest record's financials + today's price
        latest_data = flatten_financials(records[0].get("financials", [])) if records else {}
        latest_fin = records[0].get("financials", []) if records else []
        latest_eps = to_float(extract_quarterly_value(latest_fin, "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"))

        # TTM EPS = sum of last 4 quarters
        ttm_eps = None
        if len(eps_list) >= 4:
            vals = [eps_list[j] for j in range(4)]
            if all(v is not None for v in vals):
                ttm_eps = sum(vals)

        # TTM Revenue = sum of last 4 quarters
        ttm_revenue = None
        if len(rev_list) >= 4:
            vals = [rev_list[j] for j in range(4)]
            if all(v is not None for v in vals):
                ttm_revenue = sum(vals)

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
        if len(eps_list) >= 13:
            now = eps_list[0]
            old = eps_list[12]
            if now is not None and old is not None and now > 0 and old > 0:
                cagr = ((now / old) ** (1.0 / 3) - 1) * 100

        peg_growth = cagr if cagr is not None else (eps_yoy or eps_qoq)

        valuation = compute_valuation(latest_data, current_price, shares_outstanding, peg_growth, ttm_eps, latest_eps, ttm_revenue)

        result: list = []
        for rec in records:
            data = flatten_financials(rec.get("financials", []))
            static = compute_static(data)
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

    raise HTTPException(status_code=501, detail=f"Source '{source}' is not yet supported")


@app.get("/read-nse-document")
def read_nse_document(url: str, symbol: str = None):
    content = nse_scraper.read_nse_document(url, symbol=symbol)
    if content is None:
        raise HTTPException(status_code=502, detail="Failed to fetch or parse PDF from NSE")
    return {"url": url, "content": content}


@app.get("/available-web-sources")
def available_web_sources(source: Optional[str] = None):
    return {"sources": load_web_sources(source)}


@app.get("/web-source")
def web_source(id: str, symbol: str, source: str = "NSE"):
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
            "source": source.upper(),
            "symbol": symbol.upper(),
            "url": url,
            "content": resp.text,
        }
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch {url}: {e}")


if __name__ == "__main__":
    uvicorn.run(
        "api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True
    )

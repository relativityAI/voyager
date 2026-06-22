import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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
}

ALL_NSE_COLLECTIONS: Dict[str, str] = {**NSE_RAW_COLLECTIONS, **NSE_PARSED_COLLECTIONS}

XBRL_PARSE_MAP: Dict[str, str] = {
    "integrated-filing": "nse_quarterly_financials",
    "quarterly-results": "nse_quarterly_financials",
    "annual-results": "nse_annual_financials",
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

        return {
            "symbol": meta.symbol,
            "source": meta.source,
            "last_pull": meta.last_pull,
            "total_pulls": len(meta.previous_pulls) + (1 if meta.last_pull else 0),
            "previous_pulls": meta.previous_pulls,
            "record_counts": record_counts,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' is not yet supported")


@app.get("/stock-data")
async def get_stock_data(symbol: str, source: str = "NSE"):
    symbol = symbol.upper()
    source = source.upper()

    if source == "NSE":
        database = get_database()
        result: Dict[str, Any] = {}
        total = 0
        for label, coll_name in ALL_NSE_COLLECTIONS.items():
            coll = database[coll_name]
            try:
                cursor = coll.find({"symbol": symbol}, {"_id": 0}).sort("pulled_at", -1)
                records = await cursor.to_list(length=10000)
                result[label] = records
                total += len(records)
            except Exception as e:
                result[label] = str(e)

        return {
            "symbol": symbol,
            "source": source,
            "total_records": total,
            "data": result,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' is not yet supported")


@app.get("/available-metrics")
async def available_metrics(source: str = "NSE"):
    from src.ratios.nse import get_metrics_catalog
    return {
        "source": source.upper(),
        "categories": get_metrics_catalog(),
    }


@app.get("/financial-ratios")
async def financial_ratios(symbol: str, source: str = "NSE", consolidated: str = "Consolidated"):
    symbol = symbol.upper()
    source = source.upper()

    if source == "NSE":
        from src.ratios.nse import (
            ALL_CATEGORIES,
            compute_growth,
            compute_static,
            flatten_financials,
        )

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

        result: list = []
        for i, rec in enumerate(records):
            data = flatten_financials(rec.get("financials", []))
            static = compute_static(data)
            entry = {
                "date": rec.get("date"),
                "consolidated": rec.get("consolidated"),
                "source_endpoint": rec.get("source_endpoint"),
                "broadcast_date": rec.get("broadcast_date"),
                "ratios": static,
            }

            if i < len(records) - 1:
                prev_data = flatten_financials(records[i + 1].get("financials", []))
                entry["growth"] = compute_growth(data, prev_data)

            result.append(entry)

        return {
            "symbol": symbol,
            "source": source,
            "consolidated": consolidated,
            "records": result,
        }

    raise HTTPException(status_code=501, detail=f"Source '{source}' is not yet supported")


if __name__ == "__main__":
    uvicorn.run(
        "api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True
    )

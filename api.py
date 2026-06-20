import asyncio
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel

from __version__ import __version__
from src.db.connection import get_database, init_db
from src.db.models import NSEJobStatus, NSEStockMetadata
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

NSE_COLLECTION_MAP = {key: f"nse_{key.replace('-', '_')}" for key in ENDPOINTS}


async def pull_nse_data(symbol: str) -> Dict[str, Any]:
    database = get_database()
    total_records = 0
    endpoint_breakdown: Dict[str, Any] = {}

    for endpoint_key, endpoint_url in ENDPOINTS.items():
        collection = database[NSE_COLLECTION_MAP[endpoint_key]]
        count = 0

        try:
            url = endpoint_url.format(symbol=symbol) if "{symbol}" in endpoint_url else endpoint_url
            data = await asyncio.to_thread(
                lambda: nse_scraper.api._safe_json(nse_scraper.api._call(url, symbol=symbol))
            )

            records = data if isinstance(data, list) else [data] if isinstance(data, dict) else []

            for record in records:
                if not isinstance(record, dict):
                    continue
                record["symbol"] = symbol
                record["pulled_at"] = datetime.utcnow()

                stable = {k: v for k, v in record.items() if k not in ("_content_hash", "pulled_at")}
                content_hash = hashlib.md5(
                    json.dumps(stable, sort_keys=True, default=str).encode()
                ).hexdigest()
                record["_content_hash"] = content_hash

                await collection.replace_one({"_content_hash": content_hash}, record, upsert=True)
                count += 1

            total_records += count
            endpoint_breakdown[endpoint_key] = count
            logger.info(f"Pulled {count} records for {endpoint_key} ({symbol})")

        except Exception as e:
            logger.error(f"Error pulling {endpoint_key} for {symbol}: {e}")
            endpoint_breakdown[endpoint_key] = str(e)

    now = datetime.utcnow()
    meta = await NSEStockMetadata.find_one(NSEStockMetadata.symbol == symbol)
    if meta:
        if meta.last_pull:
            meta.previous_pulls.append(meta.last_pull)
        meta.last_pull = now
        meta.updated_at = now
        await meta.save()
    else:
        meta = NSEStockMetadata(symbol=symbol, last_pull=now, previous_pulls=[])
        await meta.insert()

    return {
        "symbol": symbol,
        "source": "NSE",
        "status": "completed",
        "records_pulled": total_records,
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
        for endpoint_key in ENDPOINTS:
            coll = database[NSE_COLLECTION_MAP[endpoint_key]]
            try:
                record_counts[endpoint_key] = await coll.count_documents({"symbol": symbol})
            except Exception as e:
                record_counts[endpoint_key] = str(e)

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

async def run_background_scrape(symbol: str, job_id: str):
    logger.info(f"Starting background scrape for {symbol} with job {job_id}")
    
    job = await NSEJobStatus.find_one(NSEJobStatus.job_id == job_id)
    if not job:
        return
        
    try:
        job.status = "parsing"
        job.total_fetches = 5
        await job.save()
        

        async def process_batch(data_list, category):
            financials_coll = get_database()["nse-financials"]
            for x in data_list:

                if category == "integrated" or category == "quarterly":
                    logger.info(f"Processing {category} XBRL for {symbol} - {x.get('date')} (Consolidated: {x.get('consolidated')})")
                    try:
                        parsed_data = await asyncio.to_thread(nse_scraper.process_xbrl, x, symbol, category)
                        if parsed_data:
                            existing = await financials_coll.find_one({
                                "symbol": parsed_data["symbol"],
                                "date": parsed_data["date"],
                                "consolidated": parsed_data["consolidated"],
                            })
                            if existing:
                                await financials_coll.update_one(
                                    {"_id": existing["_id"]},
                                    {"$set": {"financials": parsed_data["financials"]}},
                                )
                            else:
                                await financials_coll.insert_one(parsed_data)
                        job.completed_fetches += 1
                        await job.save()
                    except Exception as e:
                        logger.error(f"Error processing and saving XBRL: {e}")
                        job.failed_fetches += 1
                        await job.save()

                elif category == "annual":
                    logger.info(f"Processing annual report for {symbol} - {x.get('date')}")
                    # Placeholder for annual report processing logic
                    job.completed_fetches += 1
                    await job.save()

                elif category == "announcements":
                    logger.info(f"Processing announcement for {symbol} - {x.get('date')}")
                    # Placeholder for announcement processing logic
                    job.completed_fetches += 1
                    await job.save()
                elif category == "shareholdings":
                    logger.info(f"Processing shareholding for {symbol} - {x.get('date')}")
                    # Placeholder for shareholding processing logic
                    job.completed_fetches += 1
                    await job.save()
                else:
                    logger.warning(f"Unknown category {category} for {symbol}")


        # the background scraper uses sync API calls, running in a thread protects the event loop
        import asyncio
        integrated = await asyncio.to_thread(nse_scraper.api.integrated_filing_xbrls, symbol)
        integrated_data = integrated.get("data", []) if isinstance(integrated, dict) else []
        await process_batch(integrated_data, "integrated")
        
        quarterly = await asyncio.to_thread(nse_scraper.api.quarterly_results_xbrls, symbol)
        quarterly_data = quarterly.get("data", quarterly) if isinstance(quarterly, dict) else quarterly
        if not isinstance(quarterly_data, list): quarterly_data = []
        await process_batch(quarterly_data, "quarterly")

        annual = await asyncio.to_thread(nse_scraper.api.annual_results_xbrls, symbol)
        annual_data = annual.get("data", annual) if isinstance(annual, dict) else annual
        if not isinstance(annual_data, list): annual_data = []

        announcements = await asyncio.to_thread(nse_scraper.api.announcements_xbrls, symbol)
        announcements_data = announcements.get("data", announcements) if isinstance(announcements, dict) else announcements
        if not isinstance(announcements_data, list): announcements_data = []

        shareholdings = await asyncio.to_thread(nse_scraper.api.shareholding_xbrls, symbol)
        shareholdings_data = shareholdings.get("data", shareholdings) if isinstance(shareholdings, dict) else shareholdings
        if not isinstance(shareholdings_data, list): shareholdings_data = []


        job.status = "completed"
        await job.save()

    except Exception as e:
        logger.error(f"Critical error in job {job_id}: {e}")
        job.status = "failed"
        await job.save()


@app.post("/nse/scrape/{symbol}")
async def nse_scrape_endpoint(symbol: str, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    job = NSEJobStatus(job_id=job_id, symbol=symbol.upper(), status="pending")
    await job.insert()
    
    background_tasks.add_task(run_background_scrape, symbol.upper(), job_id)
    return {"message": "Scraping started.", "job_id": job_id}

@app.get("/nse/status/{job_id}")
async def nse_status_endpoint(job_id: str):
    job = await NSEJobStatus.find_one(NSEJobStatus.job_id == job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    remaining_fetches = max(0, job.total_fetches - job.completed_fetches - job.failed_fetches)
    return {
        "job_id": job.job_id,
        "symbol": job.symbol,
        "status": job.status,
        "total_fetches": job.total_fetches,
        "completed_fetches": job.completed_fetches,
        "failed_fetches": job.failed_fetches,
        "remaining_fetches": remaining_fetches,
        "created_at": job.created_at,
        "updated_at": job.updated_at
    }


if __name__ == "__main__":
    uvicorn.run(
        "api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True
    )

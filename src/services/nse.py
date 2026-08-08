import asyncio
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger
from pymongo.operations import ReplaceOne

from src.db.connection import get_database
from src.db.models import NSEStockMetadata
from src.tools.nse.client import ENDPOINTS, CookieError, NSEIndia

from ._common import (
    InvalidRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UnsupportedSourceError,
    UpstreamError,
    _filter_priority_fields,
    _load_priority_metrics,
)

STATEMENT_COLLECTIONS: Dict[str, str] = {
    "income_statements": "income_statements",
    "balance_sheets": "balance_sheets",
    "cash_flows": "cash_flows",
    "shareholdings": "shareholdings",
}

COLLECTION_TO_STMT_KEY: Dict[str, str] = {
    v: k for k, v in STATEMENT_COLLECTIONS.items()
}

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
        if not (
            filing_type
            and key in XBRL_PARSE_MAP
            and key not in FT_ENDPOINTS.get(filing_type, set())
            and key != "shareholding-pattern"
        )
    ]

    _tick("setup")

    # ---- Phase 1: fetch all endpoints (parallel) ----
    urls = [
        url.format(symbol=symbol) if "{symbol}" in url else url for _, url in selected
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
            cursor = database[coll_name].find(
                {"symbol": symbol}, {"xbrl_url": 1, "_id": 0}
            )
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
            records = sorted(
                records, key=lambda r: 1 if r.get("type_Sub") == "Revision" else 0
            )

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
            return await asyncio.to_thread(
                nse_scraper.process_xbrl, record, symbol, ep_key
            )

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
        k
        for k, v in endpoint_breakdown.items()
        if isinstance(v, str) and v not in ("no data",)
    }
    successes = {
        k for k, v in endpoint_breakdown.items() if isinstance(v, int) and v > 0
    }
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


def _validate_nse(country: str, source: str) -> None:
    if country.lower() != "in" or source != "NSE":
        raise UnsupportedSourceError(
            f"Source '{source}' for country '{country}' is not yet supported"
        )
    return source.upper()


async def get_financials(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
    all_fields: bool = False,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    _validate_nse(country, source)

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
        raise NotFoundError(f"No financial data found for {symbol}")

    return _filter_priority_fields(merged, all_priority, all_fields)


async def get_statement_data(
    route_name: str,
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: Optional[bool] = True,
    filing_type: str = "quarterly",
    limit: int = 0,
    all_fields: bool = False,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    _validate_nse(country, source)

    coll_name = ROUTE_TO_COLLECTION.get(route_name)
    if not coll_name:
        raise InvalidRequestError(f"Unknown financial statement: {route_name}")

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


async def get_pull_status(
    symbol: str, country: str = "in", source: str = "nse"
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise UnsupportedSourceError(
            f"Source '{source}' for country '{country}' is not yet supported"
        )

    meta = await NSEStockMetadata.find_one(NSEStockMetadata.symbol == symbol)
    if not meta:
        raise NotFoundError(f"No data found for {symbol}")

    database = get_database()
    record_counts: Dict[str, Any] = {}
    for label, coll_name in ALL_NSE_COLLECTIONS.items():
        try:
            record_counts[label] = await database[coll_name].count_documents(
                {"symbol": symbol}
            )
        except Exception as e:
            record_counts[label] = str(e)

    financial_breakdown: Dict[str, Any] = {}
    for coll_name in STATEMENT_COLLECTIONS.values():
        coll = database[coll_name]
        breakdown_pipeline = [
            {"$match": {"symbol": symbol}},
            {
                "$group": {
                    "_id": "$consolidated",
                    "count": {"$sum": 1},
                    "periods": {"$addToSet": "$period_end_date"},
                    "min_date": {"$min": "$period_end_date"},
                    "max_date": {"$max": "$period_end_date"},
                }
            },
        ]
        try:
            cursor = await coll.aggregate(breakdown_pipeline)
            groups = await cursor.to_list(length=10)
            if groups:
                breakdown = {}
                for g in groups:
                    cons_label = {True: "consolidated", False: "standalone"}.get(
                        g["_id"], "unknown"
                    )
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


async def get_announcements(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    market: str = "equities",
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise UnsupportedSourceError(
            f"Source '{source}' for country '{country}' is not yet supported"
        )

    ep_key = f"announcements-{market}"
    if ep_key not in ENDPOINTS:
        raise InvalidRequestError(
            f"Invalid market '{market}'. Use 'equities' or 'sme'."
        )

    try:
        url = ENDPOINTS[ep_key].format(symbol=symbol)
        data = await asyncio.to_thread(
            lambda: nse_scraper.api._safe_json(
                nse_scraper.api._call(url, symbol=symbol)
            )
        )
        if not isinstance(data, list):
            return {
                "symbol": symbol,
                "source": source,
                "market": market,
                "announcements": [],
            }

        cleaned = []
        for a in data:
            cleaned.append(
                {
                    "date": a.get("an_dt"),
                    "heading": a.get("attchmntText"),
                    "category": a.get("desc"),
                    "attachment": a.get("attchmntFile"),
                    "attachment_size": a.get("attFileSize"),
                    "has_xbrl": a.get("hasXbrl", False),
                }
            )
        return {
            "symbol": symbol,
            "source": source,
            "market": market,
            "announcements": cleaned,
        }
    except CookieError as e:
        logger.error(f"Cookie failure fetching announcements for {symbol}: {e}")
        raise ServiceUnavailableError("NSE session unavailable (cookie failure)")
    except Exception as e:
        logger.error(f"Error fetching announcements for {symbol}: {e}")
        raise UpstreamError(str(e))


async def get_shareholdings(
    symbol: str, country: str = "in", source: str = "nse"
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise UnsupportedSourceError(
            f"Source '{source}' for country '{country}' is not yet supported"
        )

    database = get_database()
    coll = database["shareholdings"]

    existing = await coll.find_one(
        {"symbol": symbol, "filing_type": "shareholding"},
        {"_id": 0},
        sort=[("period_end_date", -1)],
    )
    if existing:
        priority = _load_priority_metrics().get("shareholdings", set())
        return {
            "symbol": symbol,
            "source": source,
            "shareholdings": _filter_priority_fields(existing, priority, False),
        }

    try:
        records = await asyncio.to_thread(
            lambda: nse_scraper.api.shareholding_xbrls(symbol)
        )
        if not isinstance(records, list) or not records:
            raise NotFoundError(f"No shareholding data found for {symbol}")

        for record in records:
            parsed = await asyncio.to_thread(
                nse_scraper.process_xbrl, record, symbol, "shareholding-pattern"
            )
            if parsed is None or parsed.get("shareholding") is None:
                continue
            doc = parsed["shareholding"]
            doc["pulled_at"] = datetime.utcnow()
            await coll.replace_one(
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
            return {
                "symbol": symbol,
                "source": source,
                "shareholdings": _filter_priority_fields(doc, priority, False),
            }

        raise NotFoundError(f"No parseable shareholding XBRL found for {symbol}")
    except CookieError as e:
        logger.error(f"Cookie failure fetching shareholdings for {symbol}: {e}")
        raise ServiceUnavailableError("NSE session unavailable (cookie failure)")
    except (UnsupportedSourceError, NotFoundError, InvalidRequestError):
        raise
    except Exception as e:
        logger.error(f"Error fetching shareholdings for {symbol}: {e}")
        raise UpstreamError(str(e))

import asyncio
import os
import time
from datetime import datetime, date
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import select, func, update, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import (
    IncomeStatement,
    BalanceSheet,
    CashFlow,
    Shareholding,
    NSEStockMetadata,
)
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

STATEMENT_MODELS = {
    "income_statements": IncomeStatement,
    "balance_sheets": BalanceSheet,
    "cash_flows": CashFlow,
    "shareholdings": Shareholding,
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

METADATA_COLUMNS = {
    "symbol", "period_end_date", "period_start_date", "xbrl_url", "broadcast_date",
    "consolidated", "filing_type", "measure", "entity_identifier", "fiscal_period",
    "source_endpoint", "context_ref_type", "pulled_at", "_content_hash",
}


def _fetch_endpoint_json(url: str, symbol: str):
    res = nse_scraper.api._call(url, symbol=symbol)
    if res is None:
        return None
    return nse_scraper.api._safe_json(res)


def _parse_date(date_str):
    if date_str is None:
        return None
    if isinstance(date_str, (date, datetime)):
        return date_str if isinstance(date_str, date) else date_str.date()
    try:
        return datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_datetime(dt_str):
    if dt_str is None:
        return None
    if isinstance(dt_str, datetime):
        return dt_str
    try:
        return datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(str(dt_str), "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


def _parse_numeric(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    try:
        cleaned = str(val).replace(",", "")
        if cleaned == "" or cleaned.lower() == "null" or cleaned.lower() == "none":
            return None
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _doc_to_row(doc: Dict[str, Any], model_class) -> Dict[str, Any]:
    row = {}
    for col in model_class.__table__.columns:
        key = col.key
        if key == "id":
            continue
        if key in doc:
            val = doc[key]
            if key in ("period_end_date", "period_start_date"):
                row[key] = _parse_date(val)
            elif key == "pulled_at":
                row[key] = _parse_datetime(val) if isinstance(val, str) else val
            elif key in ("consolidated", "refresh", "enabled", "has_xbrl", "is_admin"):
                row[key] = val
            elif key == "_content_hash":
                row[key] = val
            elif key in ("previous_pulls",):
                row[key] = val or []
            elif key == "scopes":
                row[key] = val or []
            else:
                row[key] = _parse_numeric(val) if col.type.__class__.__name__ == "Numeric" else val
    return row


async def _upsert_rows(session, model_class, rows: list, on_conflict_cols: list):
    if not rows:
        return
    for row_data in rows:
        stmt = (
            pg_insert(model_class)
            .values(**row_data)
            .on_conflict_do_update(
                index_elements=on_conflict_cols,
                set_={k: v for k, v in row_data.items() if k not in ("id",) + tuple(on_conflict_cols)},
            )
        )
        await session.execute(stmt)


async def pull_nse_data(
    symbol: str, filing_type: Optional[str] = None, refresh: bool = False
) -> Dict[str, Any]:
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

    existing_urls: set = set()
    if not refresh:
        factory = get_session_factory()
        async with factory() as session:
            for model_class in (IncomeStatement, BalanceSheet, CashFlow, Shareholding):
                result = await session.execute(
                    select(model_class.xbrl_url).where(model_class.symbol == symbol)
                )
                for row in result.scalars().all():
                    if row:
                        existing_urls.add(row)
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

    rows_by_coll: Dict[str, list] = {}
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
            model_class = STATEMENT_MODELS[coll_name]
            row = _doc_to_row(doc, model_class)
            rows_by_coll.setdefault(coll_name, []).append(row)
            parsed_counts[coll_name] = parsed_counts.get(coll_name, 0) + 1
            total_parsed += 1

    factory = get_session_factory()
    async with factory() as session:
        for coll_name, rows in rows_by_coll.items():
            if rows:
                model_class = STATEMENT_MODELS[coll_name]
                await _upsert_rows(
                    session, model_class, rows,
                    on_conflict_cols=["symbol", "period_end_date", "consolidated", "source_endpoint"],
                )
                logger.info(f"Upserted {len(rows)} {coll_name} docs for {symbol}")
        await session.commit()
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
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(NSEStockMetadata).where(NSEStockMetadata.symbol == symbol)
            )
            meta = result.scalar_one_or_none()
            if meta:
                if meta.last_pull:
                    prev = list(meta.previous_pulls or [])
                    prev.append(meta.last_pull)
                    meta.previous_pulls = prev
                meta.last_pull = now
                meta.updated_at = now
                logger.info(f"Updated metadata for {symbol}")
            else:
                meta = NSEStockMetadata(
                    symbol=symbol, last_pull=now, previous_pulls=[], source="NSE",
                    created_at=now, updated_at=now,
                )
                session.add(meta)
                logger.info(f"Created metadata for {symbol}")
            await session.commit()
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

    factory = get_session_factory()
    merged: Dict[str, Any] = {"symbol": symbol, "consolidated": consolidated}

    async with factory() as session:
        for model_class in (IncomeStatement, BalanceSheet, CashFlow):
            result = await session.execute(
                select(model_class).where(
                    model_class.symbol == symbol,
                    model_class.consolidated == consolidated,
                    model_class.filing_type == filing_type,
                ).order_by(model_class.period_end_date.desc()).limit(1)
            )
            doc = result.scalar_one_or_none()
            if doc:
                d = doc.to_dict()
                for k, v in d.items():
                    if k in ("symbol", "consolidated", "pulled_at", "_content_hash", "id"):
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

    model_class = STATEMENT_MODELS[coll_name]

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(model_class).where(
            model_class.symbol == symbol,
            model_class.filing_type == filing_type,
        )
        if consolidated is not None:
            stmt = stmt.where(model_class.consolidated == consolidated)
        stmt = stmt.order_by(model_class.period_end_date.desc())
        if limit > 0:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        docs = result.scalars().all()

    records = [_filter_priority_fields(d.to_dict(), priority_set, all_fields) for d in docs]
    return {priority_key: records}


async def get_pull_status(
    symbol: str, country: str = "in", source: str = "nse"
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise UnsupportedSourceError(
            f"Source '{source}' for country '{country}' is not yet supported"
        )

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(NSEStockMetadata).where(NSEStockMetadata.symbol == symbol)
        )
        meta = result.scalar_one_or_none()
        if not meta:
            raise NotFoundError(f"No data found for {symbol}")

        record_counts: Dict[str, Any] = {}
        for label, coll_name in ALL_NSE_COLLECTIONS.items():
            model_class = STATEMENT_MODELS.get(coll_name)
            if model_class:
                count_result = await session.execute(
                    select(func.count()).select_from(model_class).where(
                        model_class.symbol == symbol
                    )
                )
                record_counts[label] = count_result.scalar() or 0

        financial_breakdown: Dict[str, Any] = {}
        for coll_name in STATEMENT_COLLECTIONS.values():
            model_class = STATEMENT_MODELS[coll_name]
            try:
                group_result = await session.execute(
                    select(
                        model_class.consolidated,
                        func.count().label("count"),
                        func.count(func.distinct(model_class.period_end_date)).label("periods"),
                        func.min(model_class.period_end_date).label("min_date"),
                        func.max(model_class.period_end_date).label("max_date"),
                    )
                    .where(model_class.symbol == symbol)
                    .group_by(model_class.consolidated)
                )
                groups = group_result.all()
                if groups:
                    breakdown = {}
                    for g in groups:
                        cons_label = {True: "consolidated", False: "standalone"}.get(
                            g.consolidated, "unknown"
                        )
                        breakdown[cons_label] = {
                            "count": g.count,
                            "periods": g.periods,
                            "date_range": f"{g.min_date} to {g.max_date}",
                        }
                    financial_breakdown[coll_name] = breakdown
            except Exception as e:
                financial_breakdown[coll_name] = str(e)

    record_total = sum(v for v in record_counts.values() if isinstance(v, int))

    return {
        "symbol": meta.symbol,
        "source": meta.source,
        "last_pull": meta.last_pull,
        "total_pulls": len(meta.previous_pulls or []) + (1 if meta.last_pull else 0),
        "previous_pulls_count": len(meta.previous_pulls or []),
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

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Shareholding).where(
                Shareholding.symbol == symbol,
                Shareholding.filing_type == "shareholding",
            ).order_by(Shareholding.period_end_date.desc()).limit(1)
        )
        existing = result.scalar_one_or_none()

    if existing:
        priority = _load_priority_metrics().get("shareholdings", set())
        return {
            "symbol": symbol,
            "source": source,
            "shareholdings": _filter_priority_fields(existing.to_dict(), priority, False),
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

            row = _doc_to_row(doc, Shareholding)
            async with factory() as session:
                stmt = (
                    pg_insert(Shareholding)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["symbol", "period_end_date", "consolidated", "source_endpoint"],
                        set_={k: v for k, v in row.items() if k not in ("id", "symbol", "period_end_date", "consolidated", "source_endpoint")},
                    )
                )
                await session.execute(stmt)
                await session.commit()

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

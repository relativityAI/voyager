"""NSE ingestion: pull XBRL statements/announcements and shareholding patterns.

Write path only — invoked by the operator tooling, never by the serving
API. Reads live in ``src.services.nse``.
"""

import asyncio
import os
import time
from datetime import timedelta
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import (
    BalanceSheet,
    CashFlow,
    FilingDisclosure,
    IncomeStatement,
    Shareholding,
    StockMetadata,
)
from src.services._common import (
    InvalidRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UnsupportedSourceError,
    UpstreamError,
)
from src.services.nse import (
    STATEMENT_MODELS,
    _load_sh_rows,
    nse_scraper,
)
from src.tools.nse.client import ENDPOINTS, CookieError
from src.utils.helpers import utcnow

from ._common import _doc_to_row, _parse_datetime, _upsert_rows

NSE_MAX_XBRL_CONCURRENCY = int(os.getenv("NSE_MAX_XBRL_CONCURRENCY", "6"))

XBRL_PARSE_MAP: Dict[str, str] = {
    "integrated-filing": "quarterly",
    "quarterly-results": "quarterly",
    "annual-results": "annual",
    "shareholding-pattern": "shareholding",
}

ANN_ENDPOINTS = ("announcements-equities", "announcements-sme")

ANN_FIELD_MAP = {
    "an_dt": "filed_at",
    "attchmntText": "title",
    "desc": "description",
    "attchmntFile": "url",
    "attFileSize": "file_size",
    "hasXbrl": "has_xbrl",
}

# Endpoints a pull actually consumes. corp-info / annual-reports / event-calendar
# are fetched (or meaningful) elsewhere, not by pull_nse_data; pulling them here
# wastes NSE rate budget and skews pull status.
PULL_ENDPOINTS = set(XBRL_PARSE_MAP) | set(ANN_ENDPOINTS)

_SH_FRESHNESS = timedelta(days=7)
# Re-running a financials pull within this window is skipped outright — the
# whole point of "don't refresh" is not re-downloading what we already have.
_FRESHNESS = timedelta(days=7)


async def _statements_fresh(symbol: str, filing_type: Optional[str]) -> bool:
    """True when statements for this symbol were pulled within `_FRESHNESS`."""
    async with get_session_factory()() as session:
        for model in (IncomeStatement, BalanceSheet, CashFlow):
            conds = [model.symbol == symbol, model.source == "NSE"]
            if filing_type:
                conds.append(model.filing_type == filing_type)
            newest = (
                await session.execute(select(func.max(model.pulled_at)).where(*conds))
            ).scalar()
            if newest and utcnow() - newest < _FRESHNESS:
                return True
    return False


def _fetch_endpoint_json(url: str, symbol: str):
    res = nse_scraper.api._call(url, symbol=symbol)
    if res is None:
        return None
    return nse_scraper.api._safe_json(res)


async def pull_nse_data(
    symbol: str, filing_type: Optional[str] = None, refresh: bool = False
) -> Dict[str, Any]:
    symbol = symbol.upper()
    if not refresh and await _statements_fresh(symbol, filing_type):
        return {"symbol": symbol, "saved": 0, "skipped": "fresh"}

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
        if key in PULL_ENDPOINTS
        and not (
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
            doc["pulled_at"] = utcnow()
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
                    on_conflict_cols=["symbol", "period_end_date", "consolidated", "source_endpoint", "source"],
                )
                logger.info(f"Upserted {len(rows)} {coll_name} docs for {symbol}")

        ann_rows = []
        for ep_key in ANN_ENDPOINTS:
            market = "sme" if ep_key.endswith("-sme") else "equities"
            for rec in raw_by_endpoint.get(ep_key, []):
                row = {
                    "symbol": symbol,
                    "exchange": "NSE",
                    "country": "IN",
                    "source": "NSE",
                    "filing_type": "announcement",
                    "market": market,
                    "raw_data": rec,
                }
                for src, col in ANN_FIELD_MAP.items():
                    if rec.get(src) is not None:
                        row[col] = rec[src]
                if "file_size" in row:
                    try:
                        row["file_size"] = int(row["file_size"])
                    except (TypeError, ValueError):
                        row["file_size"] = None
                if "filed_at" in row:
                    row["filed_at"] = _parse_datetime(row["filed_at"])
                ann_rows.append(row)
        if ann_rows:
            await _upsert_rows(
                session, FilingDisclosure, ann_rows,
                on_conflict_cols=["symbol", "source", text("md5(raw_data::text)")],
            )
            logger.info(f"Upserted {len(ann_rows)} announcements for {symbol}")

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
    elif successes:
        pull_status = "completed"
    else:
        # Every endpoint came back empty (or already in the DB): nothing to do,
        # but not an error. "no data" is not treated as a failed job.
        pull_status = "no data"

    if pull_status != "failed":
        now = utcnow()
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(StockMetadata).where(
                    StockMetadata.symbol == symbol,
                    StockMetadata.exchange == "NSE",
                    StockMetadata.country == "IN",
                )
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
                meta = StockMetadata(
                    symbol=symbol, exchange="NSE", country="IN",
                    last_pull=now, previous_pulls=[],
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

    from src.services._common import record_pull
    factory = get_session_factory()
    async with factory() as session:
        ex_row = await session.execute(
            select(StockMetadata.exchange).where(StockMetadata.symbol == symbol).limit(1)
        )
        ex = ex_row.scalar_one_or_none() or "NSE"
    await record_pull(symbol.upper(), ex, "IN")

    return {
        "symbol": symbol,
        "source": "NSE",
        "status": pull_status,
        "records_pulled": total_records,
        "xbrl_parsed": total_parsed,
        "endpoint_breakdown": endpoint_breakdown,
        "timing": timing,
    }


async def pull_announcements(symbol: str) -> Dict[str, Any]:
    """Download every NSE announcement (equities + SME) for one symbol.

    Announcements are high-frequency, so there is no freshness gate — the
    unique index on md5(raw_data) dedups, and re-running only re-fetches the
    two announcement lists. Already-stored announcements are skipped (nothing
    fetched again); the DataCatalog is refreshed with the new count.
    """
    symbol = symbol.upper()
    endpoint_breakdown: Dict[str, Any] = {}

    factory = get_session_factory()
    async with factory() as session:
        ann_rows = []
        for ep_key in ANN_ENDPOINTS:
            url = ENDPOINTS[ep_key].format(symbol=symbol)
            try:
                data = await asyncio.to_thread(_fetch_endpoint_json, url, symbol)
            except CookieError as exc:
                logger.error(f"Cookie failure for {ep_key} ({symbol}): {exc}")
                endpoint_breakdown[ep_key] = "cookie failed"
                continue
            except Exception as exc:
                logger.error(f"Error pulling {ep_key} for {symbol}: {exc}")
                endpoint_breakdown[ep_key] = str(exc)
                continue
            records = data if isinstance(data, list) else []
            endpoint_breakdown[ep_key] = len(records)
            market = "sme" if ep_key.endswith("-sme") else "equities"
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                row = {
                    "symbol": symbol,
                    "exchange": "NSE",
                    "country": "IN",
                    "source": "NSE",
                    "filing_type": "announcement",
                    "market": market,
                    "raw_data": rec,
                }
                for src, col in ANN_FIELD_MAP.items():
                    if rec.get(src) is not None:
                        row[col] = rec[src]
                if "file_size" in row:
                    try:
                        row["file_size"] = int(row["file_size"])
                    except (TypeError, ValueError):
                        row["file_size"] = None
                if "filed_at" in row:
                    row["filed_at"] = _parse_datetime(row["filed_at"])
                ann_rows.append(row)

        existing_count = await session.scalar(
            select(func.count(FilingDisclosure.id)).where(
                FilingDisclosure.symbol == symbol
            )
        ) or 0

        if ann_rows:
            await _upsert_rows(
                session, FilingDisclosure, ann_rows,
                on_conflict_cols=["symbol", "source", text("md5(raw_data::text)")],
            )
        await session.commit()

        total_count = await session.scalar(
            select(func.count(FilingDisclosure.id)).where(
                FilingDisclosure.symbol == symbol
            )
        ) or 0

    total = len(ann_rows)
    new_count = max(0, total_count - existing_count)
    skipped = total - new_count

    from src.services._common import record_pull
    factory = get_session_factory()
    async with factory() as session:
        ex_row = await session.execute(
            select(StockMetadata.exchange).where(StockMetadata.symbol == symbol).limit(1)
        )
        ex = ex_row.scalar_one_or_none() or "NSE"
    await record_pull(symbol, ex, "IN")

    if total == 0:
        status = "no data"
    elif new_count:
        status = "completed"
        logger.info(f"Stored {new_count} new announcements for {symbol} ({skipped} already stored)")
    else:
        status = "skipped"
        logger.info(f"All {skipped} announcements for {symbol} already stored — nothing fetched again")

    return {
        "symbol": symbol,
        "source": "NSE",
        "status": status,
        "records_pulled": new_count,
        "skipped": skipped,
        "endpoint_breakdown": endpoint_breakdown,
    }


async def refresh_shareholdings(symbol: str) -> Dict[str, Any]:
    """Fetch shareholding-pattern XBRLs and upsert new periods.

    Skips the fetch when the newest stored period is younger than
    ``_SH_FRESHNESS`` — the CLI runs on a cron; NSE rate budget is precious.
    """
    symbol = symbol.upper()
    existing = await _load_sh_rows(symbol)
    if existing and existing[0].pulled_at and utcnow() - existing[0].pulled_at < _SH_FRESHNESS:
        return {"symbol": symbol, "saved": 0, "skipped": "fresh"}

    factory = get_session_factory()
    try:
        records = await asyncio.to_thread(
            lambda: nse_scraper.api.shareholding_xbrls(symbol)
        )
        if not isinstance(records, list) or not records:
            raise NotFoundError(f"No shareholding data found for {symbol}")

        saved = 0
        for record in records:
            parsed = await asyncio.to_thread(
                nse_scraper.process_xbrl, record, symbol, "shareholding-pattern"
            )
            if parsed is None or parsed.get("shareholding") is None:
                continue
            doc = parsed["shareholding"]
            doc["pulled_at"] = utcnow()

            row = _doc_to_row(doc, Shareholding)
            async with factory() as session:
                stmt = (
                    pg_insert(Shareholding)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["symbol", "period_end_date", "consolidated", "source_endpoint", "source"],
                        set_={k: v for k, v in row.items() if k not in ("id", "symbol", "period_end_date", "consolidated", "source_endpoint", "source")},
                    )
                )
                await session.execute(stmt)
                await session.commit()
            saved += 1

        if not saved:
            raise NotFoundError(f"No parseable shareholding XBRL found for {symbol}")
        logger.info(f"Saved {saved} shareholding periods for {symbol}")
        return {"symbol": symbol, "saved": saved}
    except CookieError as e:
        logger.error(f"Cookie failure fetching shareholdings for {symbol}: {e}")
        raise ServiceUnavailableError("NSE session unavailable (cookie failure)")
    except (UnsupportedSourceError, NotFoundError, InvalidRequestError):
        raise
    except Exception as e:
        logger.error(f"Error fetching shareholdings for {symbol}: {e}")
        raise UpstreamError(str(e))

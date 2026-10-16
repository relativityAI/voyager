"""NSE (India) data source — READ path only.

Serves XBRL statements, announcements, shareholdings and pull status from the
DB. Writes (pulling from NSE, parsing XBRL, upserting) live in
``src.ingest.nse`` and run via the operator tooling.
"""

import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import func, or_, select

from src.db.engine import get_session_factory
from src.db.models import (
    BalanceSheet,
    CashFlow,
    IncomeStatement,
    NSEStockMetadata,
    Shareholding,
)
from src.tools.nse.client import ENDPOINTS, CookieError, NSEIndia

from ._common import (
    InvalidRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UpstreamError,
    _filter_priority_fields,
    _load_priority_metrics,
    _validate_source,
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

def _validate_nse(country: Optional[str], source: str) -> Tuple[str, str]:
    """NSE reads; SEC has its own module (src.services.sec)."""
    return _validate_source(country, source)


_STUB_BALANCE_SHEET_FIELDS = {
    "paid_up_value_of_equity_share_capital",
    "face_value_of_equity_share_capital",
    "debt_equity_ratio",
}


def _latest_populated_balance_sheet(docs, priority_fields):
    """Latest balance-sheet row that actually carries statement values.

    Current NSE quarterly XBRL filings publish only segment assets/liabilities
    and ratios (plus paid-up capital), so the newest balance-sheet row can be a
    structurally-present-but-empty stub; it must not shadow the most recent
    real balance sheet.
    """
    substantive = set(priority_fields or ()) - _STUB_BALANCE_SHEET_FIELDS
    for c in docs:
        d = c.to_dict()
        if any(d.get(f) is not None for f in substantive):
            return c
    return docs[0] if docs else None


async def _financial_history(
    symbol: str, source: str, filing_type: str,
    report_period_gte: Optional[str] = None,
    report_period_lte: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Merged doc per (period_end_date, consolidated), both bases, newest first."""
    factory = get_session_factory()
    groups: Dict[tuple, Dict[str, Any]] = {}
    async with factory() as session:
        for model_class in (IncomeStatement, BalanceSheet, CashFlow):
            stmt = select(model_class).where(
                model_class.symbol == symbol,
                model_class.filing_type == filing_type,
                model_class.source == source,
            )
            for doc in (await session.execute(stmt)).scalars().all():
                d = doc.to_dict()
                key = (str(d.get("period_end_date")), bool(d.get("consolidated")))
                g = groups.setdefault(key, {"consolidated": d.get("consolidated")})
                for k, v in d.items():
                    if k in ("symbol", "consolidated", "pulled_at", "_content_hash", "id"):
                        continue
                    g[k] = v
    # ponytail: history is loaded unpaginated in memory; ceiling is a few hundred
    # rows per symbol (NSE XBRL history) — add limit/offset if a symbol ever
    # exceeds that.
    hist = sorted(
        groups.values(),
        key=lambda g: str(g.get("period_end_date") or ""),
        reverse=True,
    )
    # ponytail: inclusive YYYY-MM-DD bounds only; extend to tz/exclusive if needed.
    def _parse_date(s: Optional[str]):
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return None
    gte_d = _parse_date(report_period_gte)
    lte_d = _parse_date(report_period_lte)
    if gte_d or lte_d:
        out_h = []
        for h in hist:
            pd = h.get("period_end_date")
            try:
                if isinstance(pd, str):
                    pdd = datetime.strptime(pd, "%Y-%m-%d")
                else:
                    continue
            except Exception:
                continue
            if gte_d and pdd < gte_d:
                continue
            if lte_d and pdd > lte_d:
                continue
            out_h.append(h)
        return out_h
    return hist


async def get_financials(
    symbol: str,
    country: Optional[str] = None,
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
    all_fields: bool = False,
    history: bool = False,
    report_period_gte: Optional[str] = None,
    report_period_lte: Optional[str] = None,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    country, source = _validate_nse(country, source)

    priority_config = _load_priority_metrics()
    all_priority = set()
    for s in priority_config.values():
        all_priority |= s

    factory = get_session_factory()
    merged: Dict[str, Any] = {"symbol": symbol, "consolidated": consolidated}
    source_periods: Dict[str, str] = {}

    async with factory() as session:
        fallback_models: list = []
        for model_class in (IncomeStatement, BalanceSheet, CashFlow):
            stmt = (
                select(model_class)
                .where(
                    model_class.symbol == symbol,
                    model_class.consolidated == consolidated,
                    model_class.filing_type == filing_type,
                    model_class.source == source,
                )
                .order_by(model_class.period_end_date.desc())
            )
            if model_class is BalanceSheet:
                docs = (await session.execute(stmt.limit(12))).scalars().all()
                doc = _latest_populated_balance_sheet(
                    docs, priority_config.get("balance_sheets", set())
                )
            else:
                doc = (await session.execute(stmt.limit(1))).scalar_one_or_none()
            if consolidated and doc is None:
                # Issuer never filed consolidated for this statement — the
                # standalone filing is the whole-company picture at that time.
                stmt = stmt.where(model_class.consolidated.is_(False))
                if model_class is BalanceSheet:
                    docs = (await session.execute(stmt.limit(12))).scalars().all()
                    doc = _latest_populated_balance_sheet(
                        docs, priority_config.get("balance_sheets", set())
                    )
                else:
                    doc = (await session.execute(stmt.limit(1))).scalar_one_or_none()
                if doc is not None:
                    fallback_models.append(model_class.__name__)
            if doc:
                d = doc.to_dict()
                period = d.get("period_end_date")
                source_periods[model_class.__name__] = str(period) if period else None
                for k, v in d.items():
                    if k in ("symbol", "consolidated", "pulled_at", "_content_hash", "id"):
                        continue
                    merged[k] = v

    if len(merged) <= 2:
        raise NotFoundError(f"No financial data found for {symbol}")

    # The income statement is the canonical reporting period; balance sheet
    # (point-in-time) and cash flow (only in annual filings for some issuers)
    # can lag. Anchor the merged period on it rather than the last model seen.
    is_period = source_periods.get("IncomeStatement")
    if is_period:
        merged["period_end_date"] = is_period

    unique_periods = {p for p in source_periods.values() if p}
    if fallback_models:
        merged["data_quality"] = {
            "warning": (
                f"No consolidated data for {', '.join(sorted(fallback_models))}; "
                "using standalone (the whole-company picture at that date)"
            ),
            "standalone_fallback": sorted(fallback_models),
        }
    elif len(unique_periods) > 1:
        merged["data_quality"] = {
            "warning": "Merged data mixes different reporting periods",
            "source_periods": source_periods,
        }

    out = _filter_priority_fields(merged, all_priority, all_fields)
    if history:
        # history=true is the complete archive: both reporting bases, so the
        # consolidated filter (which shapes the top-level snapshot) does not
        # hide standalone-only periods like PATANJALI's pre-2024 filings.
        out["history"] = await _financial_history(symbol, source, filing_type, report_period_gte=report_period_gte, report_period_lte=report_period_lte)
    return out


async def get_statement_data(
    route_name: str,
    symbol: str,
    country: Optional[str] = None,
    source: str = "nse",
    consolidated: Optional[bool] = True,
    filing_type: str = "quarterly",
    limit: int = 0,
    offset: int = 0,
    all_fields: bool = False,
    report_period_gte: Optional[str] = None,
    report_period_lte: Optional[str] = None,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    country, source = _validate_nse(country, source)

    coll_name = ROUTE_TO_COLLECTION.get(route_name)
    if not coll_name:
        raise InvalidRequestError(f"Unknown financial statement: {route_name}")

    priority_key = ROUTE_TO_PRIORITY_KEY.get(route_name)
    priority_set = _load_priority_metrics().get(priority_key, set())

    model_class = STATEMENT_MODELS[coll_name]

    factory = get_session_factory()
    async with factory() as session:
        base = select(model_class).where(
            model_class.symbol == symbol,
            model_class.filing_type == filing_type,
            model_class.source == source,
        )
        # ponytail: inclusive YYYY-MM-DD bounds only; extend to tz/exclusive if needed.
        def _parse_date(s: Optional[str]):
            if not s:
                return None
            try:
                return datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return None
        gte_d = _parse_date(report_period_gte)
        lte_d = _parse_date(report_period_lte)
        if gte_d:
            base = base.where(model_class.period_end_date >= gte_d)
        if lte_d:
            base = base.where(model_class.period_end_date <= lte_d)
        if consolidated is not None:
            if consolidated:
                # Consolidated where a consolidated period exists, otherwise that
                # period's standalone is the whole-company picture at that time.
                base = base.where(
                    or_(
                        model_class.consolidated.is_(True),
                        model_class.period_end_date.not_in(
                            select(model_class.period_end_date)
                            .where(
                                model_class.symbol == symbol,
                                model_class.filing_type == filing_type,
                                model_class.source == source,
                                model_class.consolidated.is_(True)
                            )
                        ),
                    )
                )
            else:
                base = base.where(model_class.consolidated.is_(False))

        total = (
            await session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0

        stmt = base.order_by(model_class.period_end_date.desc())
        if offset > 0:
            stmt = stmt.offset(offset)
        if limit > 0:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        docs = result.scalars().all()

    records = [_filter_priority_fields(d.to_dict(), priority_set, all_fields) for d in docs]
    return {
        priority_key: records,
        "pagination": {
            "total": total,
            "offset": offset,
            "limit": limit,
            "returned": len(records),
        },
    }


async def get_announcements(
    symbol: str,
    country: Optional[str] = None,
    source: str = "nse",
    market: str = "equities",
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    country, source = _validate_nse(country, source)

    if source == "SEC":
        from .sec import get_announcements_us

        return await get_announcements_us(symbol, country, source, market, limit, offset)

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
        if offset > 0:
            cleaned = cleaned[offset:]
        if limit is not None:
            cleaned = cleaned[:limit]
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


def _sh_payload(symbol: str, source: str, rows: List[Shareholding]) -> Dict[str, Any]:
    priority = _load_priority_metrics().get("shareholdings", set())
    return {
        "symbol": symbol,
        "source": source,
        "shareholdings": [
            _filter_priority_fields(r.to_dict(), priority, False) for r in rows
        ],
    }


async def _load_sh_rows(symbol: str) -> List[Shareholding]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Shareholding).where(
                Shareholding.symbol == symbol,
                Shareholding.filing_type == "shareholding",
            ).order_by(Shareholding.period_end_date.desc())
        )
        return list(result.scalars().all())


async def get_shareholdings(
    symbol: str, country: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    symbol = symbol.upper()
    country, source = _validate_nse(country, source)

    if source == "SEC":
        from .sec import get_shareholdings_us

        return await get_shareholdings_us(symbol, country, source)

    existing = await _load_sh_rows(symbol)
    if not existing:
        raise NotFoundError(
            f"No shareholding data for {symbol}; "
            f"ingest shareholdings for {symbol} first"
        )
    return _sh_payload(symbol, source, existing)

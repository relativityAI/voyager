import csv
import os
from typing import Any, Dict, Optional

from sqlalchemy import func, or_, select

from src.db.engine import get_session_factory
from src.db.models import BalanceSheet, CashFlow, IncomeStatement, NSEStockMetadata, Shareholding

from ._common import InvalidRequestError, _validate_source

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
SOURCES_CSV = os.path.join(ASSETS_DIR, "sources.csv")
COUNTRIES_CSV = os.path.join(ASSETS_DIR, "countries.csv")


def _load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: v.strip() for k, v in row.items()})
    return rows


LIST_PROVIDERS: Dict[str, Any] = {
    "sources": lambda: _load_csv(SOURCES_CSV),
    "countries": lambda: _load_csv(COUNTRIES_CSV),
    "industries": lambda: [],
    "sectors": lambda: [],
    "indices": lambda: [],
}


def list_category(
    category: str = "sources", country: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    country, source = _validate_source(country, source)
    category = category.lower()
    if category not in LIST_PROVIDERS:
        raise InvalidRequestError(
            f"Unknown category '{category}'. Available: {list(LIST_PROVIDERS.keys())}"
        )
    return {
        "category": category,
        "country": country,
        "source": source,
        "data": LIST_PROVIDERS[category](),
    }


async def search_symbols(
    query: str,
    country: Optional[str] = None,
    source: str = "nse",
    limit: int = 20,
) -> Dict[str, Any]:
    """Fuzzy symbol search over symbols already pulled into the DB.

    Matches by substring, case-insensitively (e.g. "relian" -> RELIANCE).
    Symbols with stored statements rank first; the stock-metadata registry
    covers pulled-but-empty symbols via a has_data flag.
    """
    query = (query or "").strip()
    if not query:
        raise InvalidRequestError("query is required")
    if len(query) < 2:
        raise InvalidRequestError("query must be at least 2 characters")
    limit = max(1, min(limit, 50))

    _, source = _validate_source(country, source)
    pattern = f"%{query.upper()}%"

    factory = get_session_factory()
    async with factory() as session:
        # Symbols that actually have statement rows, with a row count for ranking.
        stmt_stmt = (
            select(
                IncomeStatement.symbol,
                func.count().label("row_count"),
                func.max(IncomeStatement.period_end_date).label("latest_period"),
            )
            .where(
                IncomeStatement.symbol.ilike(pattern),
                IncomeStatement.source == source,
            )
            .group_by(IncomeStatement.symbol)
        )
        is_rows = (await session.execute(stmt_stmt)).all()
        is_counts = {sym: cnt for sym, cnt, _ in is_rows}
        latest_periods = {sym: latest for sym, _, latest in is_rows}
        bsb_stmt = (
            select(BalanceSheet.symbol, func.count())
            .where(BalanceSheet.symbol.ilike(pattern), BalanceSheet.source == source)
            .group_by(BalanceSheet.symbol)
        )
        cf_stmt = (
            select(CashFlow.symbol, func.count())
            .where(CashFlow.symbol.ilike(pattern), CashFlow.source == source)
            .group_by(CashFlow.symbol)
        )
        sh_stmt = (
            select(Shareholding.symbol, func.count())
            .where(Shareholding.symbol.ilike(pattern), Shareholding.source == source)
            .group_by(Shareholding.symbol)
        )

        def _counts(res):
            return {sym: cnt for sym, cnt in res.all()}

        bs_counts = _counts(await session.execute(bsb_stmt))
        cf_counts = _counts(await session.execute(cf_stmt))
        sh_counts = _counts(await session.execute(sh_stmt))

        # Metadata registry (covers pulled-but-empty symbols too).
        meta_stmt = (
            select(NSEStockMetadata)
            .where(
                NSEStockMetadata.symbol.ilike(pattern),
                NSEStockMetadata.source == source,
            )
            .limit(limit * 3)
        )
        metas = {
            m.symbol: m for m in (await session.execute(meta_stmt)).scalars().all()
        }

    results = []
    seen = set(is_counts) | set(bs_counts) | set(cf_counts) | set(sh_counts) | set(metas)
    for sym in seen:
        counts = {
            "income_statements": is_counts.get(sym, 0),
            "balance_sheets": bs_counts.get(sym, 0),
            "cash_flows": cf_counts.get(sym, 0),
            "shareholdings": sh_counts.get(sym, 0),
        }
        total = sum(counts.values())
        meta = metas.get(sym)
        latest = latest_periods.get(sym)
        results.append(
            {
                "symbol": sym,
                "source": source,
                "has_data": total > 0,
                "record_counts": counts,
                "total_records": total,
                "latest_income_period": latest.isoformat() if latest else None,
                "last_pull": meta.last_pull.isoformat() if meta and meta.last_pull else None,
            }
        )
    # Rank: symbols with real data first, then most records, then A-Z.
    results.sort(key=lambda r: (-r["total_records"], r["symbol"]))
    return {"query": query, "source": source, "count": len(results), "results": results[:limit]}

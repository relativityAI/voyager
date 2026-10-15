import csv
import os
from typing import Any, Dict, Optional

from sqlalchemy import func, or_, select

from src.db.engine import get_session_factory
from src.db.models import StockDataLedger

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
    """Fuzzy symbol search over the stock data ledger.

    Matches by substring, case-insensitively (e.g. "relian" -> RELIANCE).
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
        stmt = (
            select(StockDataLedger)
            .where(StockDataLedger.symbol.ilike(pattern))
            .order_by(StockDataLedger.total_records.desc(), StockDataLedger.symbol)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()

    results = [
        {
            "symbol": r.symbol,
            "exchange": r.exchange,
            "country": r.country,
            "price_records": r.price_records,
            "filing_records": r.filing_records,
            "announcement_records": r.announcement_records,
            "total_records": r.total_records,
            "price_last_updated": r.price_last_updated.isoformat() if r.price_last_updated else None,
            "filing_last_updated": r.filing_last_updated.isoformat() if r.filing_last_updated else None,
            "announcement_last_updated": r.announcement_last_updated.isoformat() if r.announcement_last_updated else None,
        }
        for r in rows
    ]
    return {"query": query, "source": source, "count": len(results), "results": results}

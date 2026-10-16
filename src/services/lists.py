import csv
import os
from typing import Any, Dict, Optional

from sqlalchemy import func, or_, select

from src.db.engine import get_session_factory
from src.db.models import DataCatalog

from ._common import SOURCE_COUNTRY, InvalidRequestError, _validate_source

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
SOURCES_CSV = os.path.join(ASSETS_DIR, "sources.csv")
COUNTRIES_CSV = os.path.join(ASSETS_DIR, "countries.csv")
INDICES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "indices")

# Index files scoped to source market (NSE=IN, SEC=US).
_INDEX_FILES: Dict[str, list[str]] = {
    "nse": ["nifty", "midcap", "smallcap"],
    "sec": ["nasdaq", "snp"],
}


def _load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: v.strip() for k, v in row.items()})
    return rows


def _index_entries(source: str) -> list[dict]:
    source = source.lower()
    out = []
    for index in _INDEX_FILES.get(source, []):
        path = os.path.join(INDICES_DIR, f"{index}.txt")
        count = 0
        if os.path.exists(path):
            with open(path) as f:
                count = sum(1 for line in f if line.strip())
        out.append({"index": index, "symbols": count})
    return out


def _read_index(index: str) -> list[dict]:
    path = os.path.join(INDICES_DIR, f"{index}.txt")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            parts = line.split()
            if parts:
                rows.append(
                    {
                        "symbol": parts[0],
                        "exchange": parts[1] if len(parts) > 1 else "",
                        "country": parts[2] if len(parts) > 2 else "",
                    }
                )
    return rows


# Only categories with real backing data. Each provider takes the resolved
# source so the response is scoped to it (source=nse must not list SEC).
LIST_PROVIDERS: Dict[str, Any] = {
    "sources": lambda source: [r for r in _load_csv(SOURCES_CSV) if r["id"] == source],
    "countries": lambda source: [
        r
        for r in _load_csv(COUNTRIES_CSV)
        if r["id"] == SOURCE_COUNTRY.get(source, source).lower()
    ],
    "indices": lambda source: _index_entries(source),
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
        "data": LIST_PROVIDERS[category](source),
    }


def search_indices(index: str, source: str = "nse") -> Dict[str, Any]:
    """List symbols in an index file (e.g. 'nifty', 'nasdaq').

    Scoped to source market; returns 404-style empty when the index file is
    missing for the source.
    """
    _, source = _validate_source(None, source)
    source = source.lower()
    valid = _INDEX_FILES.get(source, [])
    if index not in valid:
        raise InvalidRequestError(
            f"Unknown index '{index}' for source '{source}'. Available: {valid}"
        )
    rows = _read_index(index)
    return {"index": index, "source": source, "count": len(rows), "symbols": rows}


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
    country = _validate_source(country, source)[0].upper()
    pattern = f"%{query.upper()}%"

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(DataCatalog)
            .where(DataCatalog.symbol.ilike(pattern))
            .where(DataCatalog.country == country)
            .order_by(DataCatalog.total_records.desc(), DataCatalog.symbol)
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

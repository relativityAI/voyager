import csv
import os
from typing import Any, Dict

from ._common import InvalidRequestError

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
    category: str = "sources", country: str = "in", source: str = "nse"
) -> Dict[str, Any]:
    category = category.lower()
    if category not in LIST_PROVIDERS:
        raise InvalidRequestError(
            f"Unknown category '{category}'. Available: {list(LIST_PROVIDERS.keys())}"
        )
    return {
        "category": category,
        "country": country,
        "source": source.upper(),
        "data": LIST_PROVIDERS[category](),
    }

import importlib
from typing import Any

from src.tools.nse.client import (
    ENDPOINTS,
    NSEApiClient,
    NSEDataParser,
    NSEIndia,
    get_random_symbol,
)
from src.tools.nse.ratios import (
    ALL_CATEGORIES,
    FINANCIAL_FIELD_MAP,
    compute_growth,
    compute_static,
    extract_quarterly_value,
    flatten_financials,
    get_metrics_catalog,
)
from src.tools.nse.valuation import compute_valuation, get_valuation_catalog, to_float

# `technicals` is imported lazily: it pulls in pandas_ta, yfinance and numba,
# which cost ~0.7s of import time on every process start.
_LAZY_IMPORTS = {
    "fetch_price_info": "src.tools.nse.technicals",
    "fetch_technicals": "src.tools.nse.technicals",
    "get_technicals_catalog": "src.tools.nse.technicals",
}

__all__ = [
    "ALL_CATEGORIES",
    "ENDPOINTS",
    "FINANCIAL_FIELD_MAP",
    "NSEApiClient",
    "NSEDataParser",
    "NSEIndia",
    "compute_growth",
    "compute_static",
    "compute_valuation",
    "extract_quarterly_value",
    "fetch_price_info",
    "fetch_technicals",
    "flatten_financials",
    "get_metrics_catalog",
    "get_random_symbol",
    "get_technicals_catalog",
    "get_valuation_catalog",
    "to_float",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value

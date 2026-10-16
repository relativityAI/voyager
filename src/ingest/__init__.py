"""Ingestion pipeline: every DB write in voyager lives here.

Invoked by the operator tooling (import the package directly). The serving API
(``api.py``) and ``src.services`` never import this package — reads flow
through services, writes flow through here.
"""

from .documents import ingest_document
from .macro import (
    pull_macro_breadth,
    pull_macro_derivatives,
    pull_macro_flows,
    pull_macro_indices,
    pull_macro_index_constituents,
    pull_macro_index_history,
    pull_macro_index_quote,
    pull_macro_index_returns,
    pull_macro_index_valuation,
    pull_macro_market_valuation,
    pull_macro_overview,
    pull_macro_rates,
    pull_macro_turnover,
)
from .news import refresh_news
from .nse import pull_announcements, pull_nse_data, refresh_shareholdings
from .prices import download_and_store
from .sec import pull_sec_data

__all__ = [
    "pull_nse_data",
    "pull_sec_data",
    "download_and_store",
    "refresh_news",
    "refresh_shareholdings",
    "pull_announcements",
    "ingest_document",
    "pull_macro_overview",
    "pull_macro_indices",
    "pull_macro_index_quote",
    "pull_macro_index_history",
    "pull_macro_index_valuation",
    "pull_macro_index_returns",
    "pull_macro_index_constituents",
    "pull_macro_market_valuation",
    "pull_macro_breadth",
    "pull_macro_flows",
    "pull_macro_turnover",
    "pull_macro_derivatives",
    "pull_macro_rates",
]

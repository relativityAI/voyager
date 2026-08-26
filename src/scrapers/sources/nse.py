"""NSE (National Stock Exchange of India) source adapter.

Defines the NSE transport configuration: warm-up (cookie priming) target,
endpoint templates, referer base, validation rules, and cookie domain. The
transport itself lives in ``src/scrapers/`` and is reused by every source.

See D-03 for why the warm-up page (option-chain) differs from the API-call
Referer (get-quotes page): option-chain is only a cookie farm; Voyager's
product is financials/XBRL, and NSE's API expects the get-quotes page as the
in-page Referer.

Proxy strategy (S-04):
  - The scraping pipeline decides the proxy: Pipeline 1 (direct) uses the
    optional static ``NSE_PROXY`` env var; Pipeline 2 (residential) routes
    through a configured residential gateway. The active pipeline is chosen
    via settings / ``SCRAPING_PIPELINE`` (see ``src/scrapers/pipeline.py``).
"""

from __future__ import annotations

import os
from typing import Optional

from src.scrapers.config import SourceConfig, register_source

NSE_BASE_URL = "https://www.nseindia.com"

NSE_WARMUP_URL = f"{NSE_BASE_URL}/option-chain"
NSE_WARMUP_FALLBACKS = [
    f"{NSE_BASE_URL}/companies-listing/corporate-filings-insider-trading",
]

NSE_REFERER_BASE = f"{NSE_BASE_URL}/get-quotes/equity?symbol={{symbol}}"

NSE_ENDPOINTS = {
    "corp-info": "https://www.nseindia.com/api/corp-info?symbol={symbol}&corpType=corpInfo&market=equities",
    "shareholding-pattern": "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={symbol}",
    "announcements-equities": "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}",
    "announcements-sme": "https://www.nseindia.com/api/corporate-announcements?index=sme&symbol={symbol}",
    "annual-reports": "https://www.nseindia.com/api/annual-reports?index=equities&symbol={symbol}",
    "event-calendar": "https://www.nseindia.com/api/event-calendar",
    "quarterly-results": "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={symbol}&period=Quarterly",
    "annual-results": "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={symbol}&period=Annual",
    "integrated-filing": "https://www.nseindia.com/api/integrated-filing-results?&symbol={symbol}",
}

NSE_MARKET_ENDPOINTS = {
    "quote-equity": "https://www.nseindia.com/api/quote-equity?symbol={symbol}",
    "historical-equity": "https://www.nseindia.com/api/historical/cm/equity?symbol={symbol}&from={from_date}&to={to_date}&select=normal",
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _is_render() -> bool:
    """Detect Render deployment (Render sets RENDER env var)."""
    return os.getenv("RENDER") is not None


def build_nse_config(calls_per_second: Optional[float] = None) -> SourceConfig:
    """Build the NSE source config, honoring env knobs.

    ``calls_per_second`` (passed by callers like ``NSEIndia``) takes
    precedence; otherwise ``NSE_CALLS_PER_SECOND`` is used.

    A static ``NSE_PROXY`` env proxy is carried as ``config.proxy`` and used
    by the direct pipeline (S-04).
    """
    static_proxy = os.getenv("NSE_PROXY")

    return SourceConfig(
        name="nse",
        country="in",
        base_url=NSE_BASE_URL,
        warmup_url=NSE_WARMUP_URL,
        warmup_fallbacks=NSE_WARMUP_FALLBACKS,
        endpoints=dict(NSE_ENDPOINTS),
        referer_base=NSE_REFERER_BASE,
        cookie_domain="www.nseindia.com",
        calls_per_second=(
            calls_per_second if calls_per_second else _env_float("NSE_CALLS_PER_SECOND", 10.0)
        ),
        retries=3,
        backoff_base=0.5,
        cookie_store=os.getenv("NSE_COOKIE_STORE", "file"),
        cookie_path=os.getenv("NSE_COOKIE_PATH"),
        proxy=static_proxy,
    )


NSE_CONFIG = build_nse_config()
register_source(NSE_CONFIG)

__all__ = [
    "NSE_BASE_URL",
    "NSE_CONFIG",
    "NSE_ENDPOINTS",
    "NSE_REFERER_BASE",
    "NSE_WARMUP_URL",
    "build_nse_config",
]

"""NSE (National Stock Exchange of India) source adapter.

Defines the NSE transport configuration: warm-up (cookie priming) target,
endpoint templates, referer base, validation rules, and cookie domain. The
transport itself lives in ``src/scrapers/`` and is reused by every source.

See D-03 for why the warm-up page (option-chain) differs from the API-call
Referer (get-quotes page): option-chain is only a cookie farm; Voyager's
product is financials/XBRL, and NSE's API expects the get-quotes page as the
in-page Referer.

Proxy strategy (D-11):
  - ``NSE_PROXY`` env var: static proxy, always used when set.
  - Free proxy pool: used when ``NSE_PROXY`` is not set AND
    ``NSE_USE_FREE_PROXIES`` is true (default). On Render, the pool is
    tried first. Locally, direct connection is tried first; the pool is
    used as fallback on 403/blocked responses.
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

    Proxy resolution order:
      1. ``NSE_PROXY`` env var → static proxy (always wins).
      2. ``NSE_USE_FREE_PROXIES`` env var (default ``true``) → proxy pool.
         On Render the pool is used directly. Locally it's a fallback.
    """
    static_proxy = os.getenv("NSE_PROXY")
    use_free_proxies = os.getenv("NSE_USE_FREE_PROXIES", "true").lower() in (
        "true",
        "1",
        "yes",
    )

    pool = None
    if not static_proxy and use_free_proxies:
        from src.scrapers.proxy_pool import get_proxy_pool

        pool = get_proxy_pool()

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
        proxy_pool=pool,
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

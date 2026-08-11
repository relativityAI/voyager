"""BSE (Bombay Stock Exchange) source adapter - skeleton.

The transport configuration is defined so the source is "ready to wire": once a
BSE facade is built (mirroring ``src/tools/nse/client.py``), these knobs are all
that need adjusting. Nothing is consumed by the running system yet.

BSE notes from the research (nse-bse-api.md): BSE throttles aggressively
(~8 req/s) and its session cookie comes from a page visit; its JSON endpoints
are faster than NSE's. Endpoints are intentionally left empty until the facade
exists - filling in stale URLs is worse than none.
"""

from __future__ import annotations

import os

from src.scrapers.config import SourceConfig, register_source

BSE_BASE_URL = "https://www.bseindia.com"

BSE_CONFIG = SourceConfig(
    name="bse",
    country="in",
    base_url=BSE_BASE_URL,
    warmup_url=f"{BSE_BASE_URL}/markets/equity/EQ/Stock-Search.html",
    warmup_fallbacks=[],
    endpoints={},
    referer_base=f"{BSE_BASE_URL}/markets/equity/EQ/Stock-Search.html",
    cookie_domain="www.bseindia.com",
    calls_per_second=float(os.getenv("BSE_CALLS_PER_SECOND", "8")),
    retries=3,
    backoff_base=0.5,
    cookie_store=os.getenv("BSE_COOKIE_STORE", "file"),
    cookie_path=os.getenv("BSE_COOKIE_PATH"),
    proxy=os.getenv("BSE_PROXY"),
)

register_source(BSE_CONFIG)

__all__ = ["BSE_CONFIG"]

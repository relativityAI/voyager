"""SEC (US Securities and Exchange Commission) source adapter - skeleton.

SEC is a different class of target from NSE/BSE: it is a public, bot-friendly
site that *requires* a UA declaring the requester (see EDGAR fair-access policy,
e.g. ``Sample Company AdminContact@example.com``) and enforces a 10 req/s cap.
No cookie priming, impersonation, or stealth is wanted - the transport is still
reusable, but with ``calls_per_second=10`` and no warm-up.

Not consumed by the running system yet; exists to lock in the config shape.
"""

from __future__ import annotations

import os

from src.scrapers.config import SourceConfig, register_source

SEC_BASE_URL = "https://www.sec.gov"

SEC_CONFIG = SourceConfig(
    name="sec",
    country="us",
    base_url=SEC_BASE_URL,
    warmup_url="",
    warmup_fallbacks=[],
    endpoints={},
    referer_base=None,
    cookie_domain="",
    calls_per_second=float(os.getenv("SEC_CALLS_PER_SECOND", "10")),
    retries=3,
    backoff_base=0.5,
    cookie_store="memory",
    proxy=os.getenv("SEC_PROXY"),
    user_agent=os.getenv("SEC_USER_AGENT"),
)

register_source(SEC_CONFIG)

__all__ = ["SEC_CONFIG"]

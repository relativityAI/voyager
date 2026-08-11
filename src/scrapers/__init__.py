"""Voyager scraping transport.

Source-agnostic anti-detection HTTP layer (fingerprint, throttle, cookie
store, stealth session) plus per-source adapters under ``sources/``.

See ``docs/scraping_improvement_decisions.md`` for the reasoning behind the
design.
"""

from src.scrapers.config import SourceConfig, get_source_config, register_source
from src.scrapers.cookies import (
    CookieStore,
    FileCookieStore,
    MemoryCookieStore,
    create_cookie_store,
)
from src.scrapers.fingerprint import Fingerprint
from src.scrapers.session import (
    BlockedResponse,
    CookieError,
    SessionExhausted,
    StealthSession,
)
from src.scrapers.throttle import TokenBucketThrottle, get_throttle, reset_throttles

__all__ = [
    "BlockedResponse",
    "CookieError",
    "CookieStore",
    "FileCookieStore",
    "Fingerprint",
    "MemoryCookieStore",
    "SessionExhausted",
    "SourceConfig",
    "StealthSession",
    "TokenBucketThrottle",
    "create_cookie_store",
    "get_source_config",
    "get_throttle",
    "register_source",
    "reset_throttles",
]

"""Simple proxy pool manager.

Reads a comma-separated list of proxy URLs from the ``PROXY_POOL`` env var
and returns a random one on each call. No fetching, no validation - just a
static list from the environment (same pattern as ``src/utils/web.py``).

The :class:`ProxyPool` keeps the same interface used by the stealth session
(``get_proxy``/``mark_failed``/``mark_success``). ``mark_failed`` records a
proxy as failed for a short cooldown so ``get_proxy`` avoids returning it
again; ``mark_success`` clears the failure. This lets the stealth session
rotate to a *different* proxy when one is unstable.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PROXY_POOL_ENV = "PROXY_POOL"
PROXY_FAIL_COOLDOWN_ENV = "PROXY_POOL_FAIL_COOLDOWN"
DEFAULT_FAIL_COOLDOWN = 300.0  # seconds


class ProxyPool:
    """A static, env-configured pool of proxy URLs.

    Proxies are read once from ``PROXY_POOL`` (comma-separated) and a random
    one is returned per ``get_proxy()`` call. Proxies marked failed are
    excluded from selection for a cooldown period so rotation picks a
    different proxy.
    """

    def __init__(self, proxies: Optional[List[str]] = None) -> None:
        self._proxies = [p.strip() for p in (proxies or []) if p.strip()]
        self._failed: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._fail_cooldown = float(
            os.getenv(PROXY_FAIL_COOLDOWN_ENV, DEFAULT_FAIL_COOLDOWN)
        )

    def get_proxy(self) -> Optional[str]:
        """Return a random proxy that has not recently failed, or None if empty."""
        if not self._proxies:
            return None
        with self._lock:
            now = time.monotonic()
            candidates = [
                p
                for p in self._proxies
                if p not in self._failed
                or now - self._failed[p] > self._fail_cooldown
            ]
            if not candidates:
                # Everything failed recently - fall back to the full pool.
                candidates = self._proxies
            return random.choice(candidates)

    def mark_failed(self, proxy_url: str) -> None:
        """Record a proxy as failed so it is avoided for the cooldown period."""
        with self._lock:
            self._failed[proxy_url] = time.monotonic()

    def mark_success(self, proxy_url: str) -> None:
        """Clear a proxy's failure so it can be selected again."""
        with self._lock:
            self._failed.pop(proxy_url, None)

    def force_refresh(self) -> None:
        """No-op: static pool, nothing to refresh."""

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._proxies)


_pool: Optional[ProxyPool] = None
_pool_lock = threading.Lock()


def get_proxy_pool() -> ProxyPool:
    """Return the global proxy pool singleton, configured from ``PROXY_POOL``.

    ``PROXY_POOL`` is a comma-separated list of proxy URLs, e.g.
    ``http://proxy1:8080,http://proxy2:3128``.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            proxies = [
                p.strip()
                for p in os.getenv(PROXY_POOL_ENV, "").split(",")
                if p.strip()
            ]
            _pool = ProxyPool(proxies=proxies)
        return _pool


__all__ = [
    "ProxyPool",
    "get_proxy_pool",
]

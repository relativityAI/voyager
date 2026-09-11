"""Simple proxy pool manager.

Reads a comma-separated list of proxy URLs from the ``PROXY_POOL`` env var
and returns a random one on each call. No fetching, no validation - just a
static list from the environment (same pattern as ``src/utils/web.py``).

The :class:`ProxyPool` keeps the same interface used by the stealth session
(``get_proxy``/``mark_failed``/``mark_success``) so the transport code is
unchanged; ``mark_failed``/``mark_success`` are no-ops for a static pool.
"""

from __future__ import annotations

import logging
import os
import random
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

PROXY_POOL_ENV = "PROXY_POOL"


class ProxyPool:
    """A static, env-configured pool of proxy URLs.

    Proxies are read once from ``PROXY_POOL`` (comma-separated) and a random
    one is returned per ``get_proxy()`` call. ``mark_failed``/``mark_success``
    are no-ops kept for interface compatibility with the stealth session.
    """

    def __init__(self, proxies: Optional[List[str]] = None) -> None:
        self._proxies = [p.strip() for p in (proxies or []) if p.strip()]
        self._lock = threading.Lock()

    def get_proxy(self) -> Optional[str]:
        """Return a random proxy URL from the pool, or None if empty."""
        if not self._proxies:
            return None
        with self._lock:
            return random.choice(self._proxies)

    def mark_failed(self, proxy_url: str) -> None:
        """No-op: static pool, nothing to track."""

    def mark_success(self, proxy_url: str) -> None:
        """No-op: static pool, nothing to track."""

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

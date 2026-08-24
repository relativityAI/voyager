"""Scraping pipeline selection: direct vs residential proxy.

A pipeline answers one question: which proxy (if any) should a session route
its requests through.

- Pipeline 1 (``direct``): no proxy, or the legacy static ``NSE_PROXY``
  override from the source config. This is the current local behaviour.
- Pipeline 2 (``residential``): a configurable residential proxy gateway
  (URL + optional username/password). The proxy provider is not finalised yet,
  so this is a credential/config seam only; provider-specific session/rotation
  URLs are added when a provider is chosen.

The active pipeline and its config are kept in an in-process cache so the
(synchronous) transport never blocks on a database call. The admin API writes
the active choice to the settings table and refreshes the cache together.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, Optional

PIPELINE_NAMES = ("direct", "residential")

_lock = threading.Lock()
# Active pipeline state: {"name": ..., "config": {...}}.
_active = {"name": "direct", "config": {}}


def list_pipelines() -> list:
    """Human metadata for each known pipeline (for the admin panel/API)."""
    return [
        {
            "name": "direct",
            "label": "Direct",
            "description": "Connect directly from this machine (no proxy).",
        },
        {
            "name": "residential",
            "label": "Residential proxy",
            "description": (
                "Route through a residential proxy gateway. Configure the "
                "gateway URL and optional credentials."
            ),
        },
    ]


def _default_pipeline() -> str:
    val = os.getenv("SCRAPING_PIPELINE", "direct").strip().lower()
    return val if val in PIPELINE_NAMES else "direct"


def _residential_proxy_url(config: Dict) -> Optional[str]:
    """Build a proxy URL from residential config (or env fallbacks)."""
    url = config.get("url") or os.getenv("RESIDENTIAL_PROXY_URL")
    if url:
        return url
    host = config.get("host") or os.getenv("RESIDENTIAL_PROXY_HOST")
    if not host:
        return None
    port = config.get("port") or os.getenv("RESIDENTIAL_PROXY_PORT") or "80"
    scheme = config.get("scheme") or os.getenv("RESIDENTIAL_PROXY_SCHEME", "http")
    user = config.get("username") or os.getenv("RESIDENTIAL_PROXY_USER")
    password = config.get("password") or os.getenv("RESIDENTIAL_PROXY_PASSWORD")
    auth = f"{user}:{password}@" if user else ""
    return f"{scheme}://{auth}{host}:{port}"


def get_active_pipeline() -> Dict:
    """Return the active pipeline dict: {"name": ..., "config": {...}}."""
    with _lock:
        if _active["name"] not in PIPELINE_NAMES:
            _active["name"] = "direct"
        return {"name": _active["name"], "config": dict(_active["config"])}


def set_active_pipeline(name: str, config: Optional[Dict] = None) -> Dict:
    """Persist (caller writes to the DB) and refresh the in-process cache."""
    name = (name or "").strip().lower()
    if name not in PIPELINE_NAMES:
        raise ValueError(f"Unknown pipeline '{name}'. Valid: {list(PIPELINE_NAMES)}")
    with _lock:
        _active["name"] = name
        _active["config"] = dict(config or {})
    return get_active_pipeline()


def default_reset() -> None:
    """Reset cache to env default 'direct' (used at startup/lifecycle)."""
    with _lock:
        _active["name"] = _default_pipeline()
        _active["config"] = {}


def proxy_resolver_for(config) -> callable:
    """Return a zero-arg resolver returning the active pipeline's proxy.

    ``config`` is a :class:`src.scrapers.config.SourceConfig`; the direct
    pipeline falls back to its legacy static ``proxy`` value (``NSE_PROXY``).
    """

    def resolve() -> Optional[str]:
        name = get_active_pipeline()["name"]
        if name == "residential":
            return _residential_proxy_url(get_active_pipeline()["config"])
        return getattr(config, "proxy", None)

    return resolve


__all__ = [
    "PIPELINE_NAMES",
    "default_reset",
    "get_active_pipeline",
    "list_pipelines",
    "proxy_resolver_for",
    "set_active_pipeline",
]
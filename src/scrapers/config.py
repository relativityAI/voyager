"""Source configuration and registry.

A :class:`SourceConfig` fully describes how to talk to one data source
(exchange/agency): the transport knobs (impersonation, rate, retries, cookie
store) and the source-specific endpoints. The transport is source-agnostic;
sources plug in via config (see D-09).

The registry maps ``(country, source)`` → config and lazy-imports source
modules so adding a source never requires touching the transport.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.scrapers.cookies import create_cookie_store
from src.scrapers.fingerprint import Fingerprint, get_impersonate
from src.scrapers.throttle import get_throttle


@dataclass
class SourceConfig:
    name: str
    country: str
    base_url: str
    warmup_url: str
    endpoints: Dict[str, str] = field(default_factory=dict)
    warmup_fallbacks: List[str] = field(default_factory=list)
    impersonate: str = "chrome131"
    user_agent: Optional[str] = None
    calls_per_second: float = 10.0
    burst: int = 4
    referer_base: Optional[str] = None
    cookie_domain: str = ""
    retries: int = 3
    backoff_base: float = 0.5
    cookie_store: str = "file"
    cookie_path: Optional[str] = None
    proxy: Optional[str] = None
    extra_headers: Dict[str, str] = field(default_factory=dict)

    @property
    def env_prefix(self) -> str:
        return self.name.upper()

    def build_fingerprint(self) -> Fingerprint:
        return Fingerprint(
            impersonate=get_impersonate(self.name) or self.impersonate,
            user_agent=self.user_agent,
            extra_headers=self.extra_headers or None,
        )

    def build_throttle(self):
        return get_throttle(self.name, self.calls_per_second, self.burst)

    def build_cookie_store(self):
        return create_cookie_store(
            self.name,
            cookie_store=self.cookie_store
            or os.getenv(f"{self.env_prefix}_COOKIE_STORE", None),
        )


_registry: Dict[str, SourceConfig] = {}
_loaded_sources: set = set()


def register_source(config: SourceConfig) -> None:
    key = (config.country.lower(), config.name.lower())
    _registry[key] = config


def get_source_config(country: str, source: str) -> Optional[SourceConfig]:
    """Resolve (country, source) to its config, lazy-importing the module."""
    country = country.lower()
    source = source.lower()
    key = (country, source)

    if key in _registry:
        return _registry[key]

    if source not in _loaded_sources:
        _loaded_sources.add(source)
        try:
            import importlib

            importlib.import_module(f"src.scrapers.sources.{source}")
        except ImportError:
            return None

    return _registry.get(key)


__all__ = ["SourceConfig", "get_source_config", "register_source"]

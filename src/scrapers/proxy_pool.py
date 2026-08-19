"""Free proxy pool manager for NSE scraping.

Fetches, validates, and rotates proxies from free proxy lists on GitHub.
Used as a fallback when direct connections are blocked (e.g., on cloud
providers like Render where NSE blocks datacenter IPs).

Priority order:
  1. Indian residential proxies (ISP ASNs like Jio, BSNL, Airtel)
  2. Indian datacenter proxies
  3. Other non-datacenter proxies
  4. Datacenter proxies (last resort)

Design decisions:
  - On-demand refresh (no background thread). Refresh when pool is empty
    or stale (>5 min old).
  - Validate against NSE warmup URL before returning a proxy.
  - Thread-safe. Global singleton via get_proxy_pool().
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import requests as _requests

logger = logging.getLogger(__name__)

IPLISTATE_RAW_URL = (
    "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt"
)
CLEARPROXY_JSON_URL = (
    "https://raw.githubusercontent.com/ClearProxy/checked-proxy-list/main/http/json/all.json"
)

INDIAN_ISP_ASNS: set = {55836, 9829, 45648, 132203}

DATACENTER_ASNS: set = {8075, 14618, 16276, 24940, 45102, 53813}

DEFAULT_VALIDATE_URL = "https://www.nseindia.com/option-chain"
DEFAULT_FETCH_TTL = 300.0
DEFAULT_VALIDATE_TIMEOUT = 5.0
MAX_VALIDATE_CANDIDATES = 5
MAX_FAIL_COUNT = 3


@dataclass
class ProxyInfo:
    ip: str
    port: int
    protocol: str = "http"
    country_code: str = ""
    asn: Optional[int] = None
    isp: str = ""
    speed_ms: Optional[float] = None
    anonymity: str = ""
    last_validated: float = 0.0
    fail_count: int = 0

    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def is_indian(self) -> bool:
        return self.country_code.upper() == "IN"

    @property
    def priority(self) -> int:
        if self.is_indian and self.asn in INDIAN_ISP_ASNS:
            return 0
        if self.is_indian:
            return 1
        if self.asn and self.asn not in DATACENTER_ASNS:
            return 2
        return 3


def _parse_iplocate_line(line: str) -> Optional[ProxyInfo]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        from urllib.parse import urlparse

        parsed = urlparse(line)
        if not parsed.hostname or not parsed.port:
            return None
        return ProxyInfo(
            ip=parsed.hostname,
            port=parsed.port,
            protocol=parsed.scheme or "http",
        )
    parts = line.split(":")
    if len(parts) != 2:
        return None
    ip, port_str = parts
    try:
        port = int(port_str)
    except ValueError:
        return None
    return ProxyInfo(ip=ip, port=port, protocol="http")


def _fetch_iplocate(session: _requests.Session) -> List[ProxyInfo]:
    proxies: List[ProxyInfo] = []
    try:
        resp = session.get(IPLISTATE_RAW_URL, timeout=10)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            info = _parse_iplocate_line(line)
            if info:
                proxies.append(info)
    except Exception as exc:
        logger.debug("Failed to fetch iplocate proxies: %s", exc)
    return proxies


def _fetch_clearproxy(session: _requests.Session) -> List[ProxyInfo]:
    proxies: List[ProxyInfo] = []
    try:
        resp = session.get(CLEARPROXY_JSON_URL, timeout=10)
        resp.raise_for_status()
        for item in resp.json():
            ip = item.get("ip", "")
            port = item.get("port", 0)
            if not ip or not port:
                continue
            raw_asn = item.get("asn")
            asn = int(raw_asn) if isinstance(raw_asn, (int, float)) else None
            raw_speed = item.get("speed_ms")
            speed = float(raw_speed) if raw_speed else None
            proxies.append(
                ProxyInfo(
                    ip=ip,
                    port=int(port),
                    protocol=item.get("protocol", "http"),
                    country_code=item.get("country_code", ""),
                    asn=asn,
                    isp=item.get("isp", ""),
                    speed_ms=speed,
                    anonymity=item.get("anonymity", ""),
                )
            )
    except Exception as exc:
        logger.debug("Failed to fetch ClearProxy proxies: %s", exc)
    return proxies


def _dedupe(proxies: List[ProxyInfo]) -> List[ProxyInfo]:
    seen: set = set()
    unique: List[ProxyInfo] = []
    for p in proxies:
        key = (p.ip, p.port)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


class ProxyPool:
    def __init__(
        self,
        validate_url: str = DEFAULT_VALIDATE_URL,
        fetch_ttl: float = DEFAULT_FETCH_TTL,
    ) -> None:
        self._validate_url = validate_url
        self._fetch_ttl = fetch_ttl
        self._proxies: List[ProxyInfo] = []
        self._lock = threading.Lock()
        self._last_fetch: float = 0.0
        self._http = _requests.Session()
        self._http.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            }
        )

    def _fetch_all(self) -> List[ProxyInfo]:
        all_proxies = _fetch_iplocate(self._http) + _fetch_clearproxy(self._http)
        all_proxies = _dedupe(all_proxies)
        all_proxies.sort(key=lambda p: (p.priority, p.speed_ms or 9999.0))
        return all_proxies

    def _ensure_fresh(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_fetch < self._fetch_ttl:
                return
        new_proxies = self._fetch_all()
        with self._lock:
            if time.monotonic() - self._last_fetch < self._fetch_ttl:
                return
            self._proxies = new_proxies
            self._last_fetch = time.monotonic()
            logger.info("Proxy pool refreshed: %d proxies", len(new_proxies))

    def _validate(self, proxy: ProxyInfo) -> bool:
        try:
            resp = self._http.get(
                self._validate_url,
                proxies={"http": proxy.url, "https": proxy.url},
                timeout=DEFAULT_VALIDATE_TIMEOUT,
            )
            if resp.status_code < 400:
                proxy.last_validated = time.monotonic()
                proxy.fail_count = 0
                return True
        except Exception:
            pass
        proxy.fail_count += 1
        return False

    def get_proxy(self) -> Optional[str]:
        self._ensure_fresh()
        with self._lock:
            candidates = list(self._proxies)
        if not candidates:
            return None
        now = time.monotonic()
        for p in candidates:
            if (
                p.last_validated > 0
                and now - p.last_validated < self._fetch_ttl
                and p.fail_count < MAX_FAIL_COUNT
            ):
                return p.url
        for p in candidates[:MAX_VALIDATE_CANDIDATES]:
            if p.fail_count < MAX_FAIL_COUNT and self._validate(p):
                return p.url
        return None

    def mark_failed(self, proxy_url: str) -> None:
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.fail_count += 1
                    break

    def mark_success(self, proxy_url: str) -> None:
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.last_validated = time.monotonic()
                    p.fail_count = 0
                    break

    def force_refresh(self) -> None:
        with self._lock:
            self._last_fetch = 0.0
        self._ensure_fresh()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._proxies)


_pool: Optional[ProxyPool] = None
_pool_lock = threading.Lock()


def get_proxy_pool() -> ProxyPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProxyPool()
        return _pool


__all__ = [
    "ProxyInfo",
    "ProxyPool",
    "get_proxy_pool",
]

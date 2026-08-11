"""Stealth HTTP session - the anti-detection transport.

:class:`StealthSession` layers the research-validated techniques onto
``curl_cffi`` (see D-01..D-08):

- TLS/browser fingerprint impersonation (``impersonate=``).
- One stable session identity (UA is never rotated mid-cookie-life, D-02).
- Cookie priming on a real HTML page, once per TTL (D-03).
- Persistent cookie store with graceful fallback (D-04).
- Coherent page-load vs API (fetch) header sets (D-05).
- Global per-source token-bucket throttle with jitter (D-06).
- Retry semantics: 401/403 → re-prime, 429 → backoff without clearing cookies
  (D-07); optional per-request ``validate`` hook for body/content-type checks
  (D-08).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Dict, Optional

from curl_cffi.requests import Session as CurlSession
from curl_cffi.requests.exceptions import RequestException

from src.scrapers.config import SourceConfig


class CookieError(Exception):
    """Raised when a session cookie could not be established."""


class SessionExhausted(Exception):
    """Raised when all retry attempts fail without a usable response."""


class BlockedResponse(Exception):
    """Raised by a validate hook when a response is a block/not-ready page.

    Content-type sniffing uses this so an HTML block page masquerading as a
    file is treated as a failure and retried instead of being saved (D-08).
    """


DEFAULT_TIMEOUT = 10.0
DEFAULT_PRIME_TIMEOUT = 15.0
DEFAULT_PRIME_TTL = 1800.0  # 30 minutes


class StealthSession:
    """Thread-safe stealth transport for one source."""

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.fingerprint = config.build_fingerprint()
        self.throttle = config.build_throttle()
        self.cookie_store = config.build_cookie_store()
        self.cookie_domain = getattr(config, "cookie_domain", None) or ""
        self.timeout = DEFAULT_TIMEOUT
        self.prime_timeout = DEFAULT_PRIME_TIMEOUT
        self.prime_ttl = DEFAULT_PRIME_TTL
        self.logger = logging.getLogger(__name__)
        self._session: Optional[CurlSession] = None
        self._lock = threading.RLock()
        self._primed_at: Optional[float] = None
        self._cookies_loaded = False

    # ------------------------------------------------------------------ setup

    @property
    def session(self) -> CurlSession:
        """Lazily create the underlying curl_cffi session and restore cookies."""
        with self._lock:
            if self._session is None:
                sess = CurlSession()
                sess.impersonate = self.fingerprint.impersonate
                self._session = sess
            if not self._cookies_loaded:
                self._cookies_loaded = True
                for name, value in self.cookie_store.load().items():
                    try:
                        self._session.cookies.set(
                            name, value, domain=self.cookie_domain
                        )
                    except Exception as exc:  # noqa: BLE001 - a bad cookie must not block startup
                        self.logger.debug(f"Ignoring invalid persisted cookie {name}: {exc}")
            return self._session

    # ----------------------------------------------------------------- cookie lifecycle

    def _save_cookies(self) -> None:
        try:
            snapshot = dict(self.session.cookies)
        except Exception:  # noqa: BLE001
            snapshot = {}
        if snapshot:
            self.cookie_store.save(snapshot)

    def _invalidate_cookies(self) -> None:
        with self._lock:
            self._primed_at = None
            try:
                self.session.cookies.clear()
            except Exception:  # noqa: BLE001
                pass
            self.cookie_store.save({})

    def _do_prime(self) -> bool:
        """GET a real HTML page to obtain the WAF session cookie (D-03)."""
        targets = [self.config.warmup_url, *self.config.warmup_fallbacks]
        last_error: str = ""
        for url in targets:
            self.throttle.wait()
            try:
                resp = self.session.get(
                    url,
                    headers=self.fingerprint.page_load_headers(),
                    timeout=self.prime_timeout,
                )
                if resp.status_code == 200 and dict(self.session.cookies):
                    self._primed_at = time.monotonic()
                    self._save_cookies()
                    return True
                last_error = f"{url} -> {resp.status_code}"
            except RequestException as exc:
                last_error = f"{url} -> {exc}"
            except Exception as exc:  # noqa: BLE001 - any failure means "not primed"
                last_error = f"{url} -> {exc}"
        self.logger.warning(f"Cookie priming failed for {self.config.name}: {last_error}")
        return False

    def prime(self, force: bool = False) -> bool:
        """Ensure the session is primed (once per TTL unless forced)."""
        with self._lock:
            if not force and self._primed_at is not None:
                if time.monotonic() - self._primed_at < self.prime_ttl:
                    return True
            return self._do_prime()

    def _ensure_primed_or_raise(self) -> None:
        if not self.prime():
            raise CookieError(
                f"Could not establish session cookies for {self.config.name} "
                f"(priming failed for {self.config.warmup_url})"
            )

    # ----------------------------------------------------------------- requests

    def _backoff(self, attempt: int) -> None:
        delay = self.config.backoff_base * (2 ** max(attempt, 0))
        delay *= 1.0 + random.uniform(-0.2, 0.2)
        time.sleep(max(delay, 0.0))

    def request(
        self,
        method: str,
        url: str,
        *,
        referer: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        allow_redirects: bool = True,
        validate: Optional[Callable] = None,
    ):
        """Perform a throttled, primed, retried request (D-06/D-07/D-08).

        Returns the response on success. Raises :class:`CookieError` if the
        session cookie cannot be established, or :class:`SessionExhausted` if
        all attempts fail. Callers that want ``None`` on exhaustion catch
        ``SessionExhausted`` (the NSE facade does exactly this).
        """
        request_timeout = timeout if timeout is not None else self.timeout
        last_failure: str = ""
        reprimed = False
        proxies = self.config.proxy or None

        for attempt in range(self.config.retries):
            self.throttle.wait()
            self._ensure_primed_or_raise()

            req_headers = self.fingerprint.api_headers(referer)
            if headers:
                req_headers.update(headers)

            try:
                resp = self.session.request(
                    method,
                    url,
                    headers=req_headers,
                    timeout=request_timeout,
                    allow_redirects=allow_redirects,
                    proxies=proxies,
                )
            except RequestException as exc:
                last_failure = f"network error: {exc}"
                self.logger.warning(f"{method} {url} failed ({last_failure}); retrying")
                self._backoff(attempt)
                continue

            if validate is not None:
                try:
                    validate(resp)
                except Exception as exc:  # noqa: BLE001 - validation failure == retry
                    last_failure = f"validation: {exc}"
                    self.logger.warning(f"{method} {url} {last_failure}; retrying")
                    self._backoff(attempt)
                    continue

            status = resp.status_code

            if status in (401, 403):
                if not reprimed:
                    reprimed = True
                    self.logger.warning(
                        f"Got {status} from {url}; clearing cookies and re-priming"
                    )
                    self._invalidate_cookies()
                    self._do_prime()
                    self._backoff(attempt)
                    continue
                raise CookieError(
                    f"Session rejected with {status} for {self.config.name} "
                    f"even after re-prime: {url}"
                )

            if status == 429:
                # Rate-limit signal - back off but do NOT clear cookies (D-07).
                last_failure = f"HTTP {status}"
                self.logger.warning(f"Rate limited ({status}) on {url}; backing off")
                self._backoff(attempt)
                continue

            if status >= 500:
                last_failure = f"HTTP {status}"
                self.logger.warning(f"Server error {status} on {url}; retrying")
                self._backoff(attempt)
                continue

            return resp

        raise SessionExhausted(
            f"{method} {url} failed after {self.config.retries} attempts "
            f"(last failure: {last_failure or 'unknown'})"
        )

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:  # noqa: BLE001
                    pass
                self._session = None


__all__ = [
    "BlockedResponse",
    "CookieError",
    "SessionExhausted",
    "StealthSession",
]

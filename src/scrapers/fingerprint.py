"""Browser fingerprint profiles for stealth HTTP clients.

A fingerprint is a coherent set of signals a real browser sends: the TLS
impersonation target (curl-cffi ``impersonate``), a User-Agent matching that
target, and the standard page-load / API (fetch) header sets.

Why this exists: NSE's WAF fingerprints the TLS handshake and the coherence of
headers (UA + language + encoding must all come from the same browser family),
and expects two distinct header profiles - page-load headers for a warm-up GET
and fetch()/API headers (Referer + sec-fetch-*) for data calls.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

# curl-cffi impersonation targets. Prefer recent, maintained profiles - WAFs
# learn old TLS fingerprints over time (see D-10).
DEFAULT_IMPERSONATE = "chrome131"

# A UA consistent with the DEFAULT_IMPERSONATE profile. Kept stable per session;
# rotating it mid-cookie-life invalidates the primed session cookie (see D-02).
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_COMMON_HEADERS = {
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Headers a browser sends when it first navigates to a page.
_PAGE_LOAD_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,*/*;q=0.8"
)

# Headers a browser's same-origin fetch() sends when the page calls its own API.
_API_ACCEPT = "application/json, text/plain, */*"


def get_impersonate(source: str) -> str:
    """Resolve the impersonation profile for a source (env-overridable)."""
    return os.getenv(f"{source.upper()}_IMPERSONATE", DEFAULT_IMPERSONATE)


class Fingerprint:
    """A per-session browser identity: impersonation target + stable UA.

    All headers derived from one instance share a coherent UA, so a primed
    cookie is always replayed under the same fingerprint.
    """

    def __init__(
        self,
        impersonate: Optional[str] = None,
        user_agent: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.impersonate = impersonate or DEFAULT_IMPERSONATE
        self.user_agent = user_agent or DEFAULT_UA
        self._common = dict(_COMMON_HEADERS)
        if extra_headers:
            self._common.update(extra_headers)

    @property
    def common_headers(self) -> Dict[str, str]:
        return dict(self._common)

    def page_load_headers(self) -> Dict[str, str]:
        """Headers for the initial warm-up GET (a real page navigation)."""
        headers = dict(self._common)
        headers["User-Agent"] = self.user_agent
        headers["Accept"] = _PAGE_LOAD_ACCEPT
        return headers

    def api_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        """Headers for a same-origin API/fetch call.

        ``referer`` should be a real page on the target origin so each API call
        looks like an in-page AJAX request.
        """
        headers = dict(self._common)
        headers["User-Agent"] = self.user_agent
        headers["Accept"] = _API_ACCEPT
        headers["sec-fetch-dest"] = "empty"
        headers["sec-fetch-mode"] = "cors"
        headers["sec-fetch-site"] = "same-origin"
        if referer:
            headers["Referer"] = referer
        return headers

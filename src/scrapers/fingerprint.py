"""Browser fingerprint profiles for stealth HTTP clients.

A fingerprint is a coherent set of signals a real browser sends: the TLS
impersonation target (curl-cffi ``impersonate``), a User-Agent matching that
target, Client Hints (``sec-ch-ua*``), and the standard page-load / API
(fetch) header sets.

Why this exists: NSE's WAF (Akamai Bot Manager) fingerprints the TLS
handshake, the coherence of headers (UA + language + encoding + Client Hints
must all come from the same browser family), and expects two distinct header
profiles - page-load headers for a warm-up GET and fetch()/API headers
(Referer + sec-fetch-*) for data calls.
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

# Client Hints (sec-ch-ua*) per impersonation target. Akamai Bot Manager
# fingerprints these on every request, so they must stay coherent with the UA
# and the TLS profile. Fall back to Chrome 131 hints for unknown targets.
_CLIENT_HINTS_BY_TARGET: Dict[str, Dict[str, str]] = {
    "chrome131": {
        "sec-ch-ua": (
            '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"15.0.0"',
        "sec-ch-ua-full-version-list": (
            '"Google Chrome";v="131.0.6778.86", "Chromium";v="131.0.6778.86", '
            '"Not_A Brand";v="24.0.0.0"'
        ),
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-model": '""',
        "sec-ch-ua-wow64": "?0",
    },
}

_COMMON_HEADERS = {
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Priority": "u=0, i",
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
        # Client Hints must match the impersonation target (Akamai cross-checks).
        self._common.update(_CLIENT_HINTS_BY_TARGET.get(self.impersonate, {}))
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
        # A top-level navigation: document / navigate / none + user gesture.
        headers["sec-fetch-dest"] = "document"
        headers["sec-fetch-mode"] = "navigate"
        headers["sec-fetch-site"] = "none"
        headers["sec-fetch-user"] = "?1"
        headers["Cache-Control"] = "max-age=0"
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

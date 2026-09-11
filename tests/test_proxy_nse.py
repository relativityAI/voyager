"""Live proxy test: verifies NSE access through a configured proxy.

This test hits a real NSE endpoint through a real proxy, so it is marked
``live`` and does not run in the normal test suite. Run it explicitly:

    python -m pytest tests/test_proxy_nse.py -m live -v

The proxy list defaults to a comma-separated set of free proxies and can be
overridden with ``TEST_PROXY_URL``. On failure the session rotates to the
next proxy in the pool (up to 3 times).
"""

from __future__ import annotations

import os

import pytest

from src.scrapers.proxy_pool import ProxyPool
from src.scrapers.session import BlockedResponse, StealthSession
from src.scrapers.sources.nse import build_nse_config

PROXY_URLS = [
    p.strip()
    for p in os.getenv(
        "TEST_PROXY_URL",
        "http://117.236.124.166:3128,http://202.28.194.139:31280,"
        "http://185.195.71.218:18080,http://1.231.81.166:3128",
    ).split(",")
    if p.strip()
]
SYMBOL = os.getenv("TEST_NSE_SYMBOL", "TCS")


def _validate_api_response(resp) -> None:
    """Treat an HTML block page as a failure so the session retries."""
    ctype = resp.headers.get("content-type", "")
    if "text/html" in ctype.lower():
        raise BlockedResponse(f"NSE returned HTML (blocked): {ctype}")


@pytest.mark.live
def test_nse_endpoint_through_proxy():
    """Access a real NSE API endpoint through the configured proxy."""
    config = build_nse_config(calls_per_second=5)
    config.proxy_pool = ProxyPool(proxies=PROXY_URLS)
    session = StealthSession(config, force_proxy=True)

    try:
        # Prime the WAF cookie through the proxy.
        assert session.prime(force=True), "cookie priming failed through proxy"

        # Hit a real NSE API endpoint through the proxy.
        url = config.endpoints["announcements-equities"].format(symbol=SYMBOL)
        resp = session.get(
            url,
            referer=config.referer_base.format(symbol=SYMBOL),
            validate=_validate_api_response,
        )
        assert resp.status_code == 200
        ctype = resp.headers.get("content-type", "")
        assert "text/html" not in ctype.lower(), f"NSE blocked the request: {ctype}"
        # Confirm the request actually went through one of the pool proxies.
        assert session._current_proxy in PROXY_URLS
    finally:
        session.close()
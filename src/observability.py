"""Observability: Sentry error tracking + Prometheus metrics.

Everything is env-gated so a deploy without accounts still runs fine:
- SENTRY_DSN set  -> error tracking enabled
- METRICS_ENABLED -> /metrics served and request metrics collected
"""

import hashlib
import json
import os
import time

from fastapi import Request
from fastapi.responses import Response
from loguru import logger
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

# GET paths whose responses are server-cached/semi-static — they get an ETag
# (conditional-request support) and short Cache-Control (audit P2-10).
_CACHEABLE_GET_PATHS = {"/news/stories", "/news/ticker", "/list", "/announcements"}
# Macro sub-routes that are immutable for a given date window (historical series,
# whole-market valuation, RBI rate snapshot). Live routes (/macro/overview,
# /macro/indices, /macro/indices/{symbol}, /macro/flows, ...) stay uncached.
_MACRO_CACHEABLE_SUFFIXES = ("/history", "/valuation", "/returns")
_CACHE_MAX_AGE = 120


def _is_cacheable_path(path: str) -> bool:
    if path in _CACHEABLE_GET_PATHS:
        return True
    if path == "/macro/valuation" or path == "/macro/rates":
        return True
    return path.startswith("/macro/indices/") and path.endswith(_MACRO_CACHEABLE_SUFFIXES)


def _etag_for(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:24] + '"'


class HttpCacheMiddleware(BaseHTTPMiddleware):
    """ETag + Cache-Control for cacheable GET endpoints.

    Strong ETag over the exact response body: clients can send If-None-Match
    and get a bodyless 304, and browsers/CDNs get an explicit freshness hint.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method != "GET" or not _is_cacheable_path(request.url.path):
            return await call_next(request)

        response = await call_next(request)
        if response.status_code != 200:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        etag = _etag_for(body)
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        response.headers["ETag"] = etag
        # Compare tolerant of a W/ weak prefix: some proxies (e.g. Render's)
        # rewrite ETags to weak form, and RFC 7232 says a weak comparison is
        # valid for If-None-Match on GET.
        if_none = request.headers.get("if-none-match", "").strip()
        if if_none.startswith("W/"):
            if_none = if_none[2:]
        if if_none == etag:
            return Response(status_code=304, headers=dict(response.headers))
        return Response(
            content=body,
            status_code=200,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests served",
    ["method", "route", "status"],
)
DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "route"],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            # Expose the per-key rate-limit window on every keyed response
            # (audit P2-12) so clients can self-throttle.
            rl = getattr(request.state, "rate_limit_info", None)
            if rl:
                for k, v in rl.items():
                    response.headers.setdefault(k, str(v))
            return response
        finally:
            route = "unmatched"
            matched = request.scope.get("route")
            if matched is not None and getattr(matched, "path", None):
                route = matched.path
            DURATION.labels(request.method, route).observe(time.perf_counter() - start)
            REQUESTS.labels(request.method, route, status).inc()


def init_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        logger.info("SENTRY_DSN not set; Sentry disabled")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.loguru import LoguruIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("ENVIRONMENT", "production"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoguruIntegration(),
            ],
        )
        logger.info("Sentry enabled")
    except Exception as exc:  # noqa: BLE001 - never let observability break the app
        logger.warning(f"Failed to initialize Sentry: {exc}")


def metrics_enabled() -> bool:
    return os.getenv("METRICS_ENABLED", "true").lower() in ("1", "true", "yes")


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

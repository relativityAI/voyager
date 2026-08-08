"""Observability: Sentry error tracking + Prometheus metrics.

Everything is env-gated so a deploy without accounts still runs fine:
- SENTRY_DSN set  -> error tracking enabled
- METRICS_ENABLED -> /metrics served and request metrics collected
"""

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
from starlette.types import ASGIApp

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
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
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


def init_observability() -> None:
    init_sentry()


def metrics_enabled() -> bool:
    return os.getenv("METRICS_ENABLED", "true").lower() in ("1", "true", "yes")


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

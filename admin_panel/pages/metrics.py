"""Metrics page: Prometheus /metrics broken down by route.

Only the /bucket samples of the histogram family are latency samples; _sum
and _count are aggregations of the same observations and must not be counted
again.
"""

import pandas as pd
import streamlit as st
from prometheus_client.parser import text_string_to_metric_families

from admin_panel.state import build_client, render_result, sidebar, submit

COUNTER_FAMILY = "http_requests"
HISTOGRAM_FAMILY = "http_request_duration_seconds"


def percentile(bucket_list, p: float):
    """Interpolated percentile from cumulative histogram buckets.

    bucket_list is [(le, cumulative_count), ...] ascending, +Inf last.
    Returning the bucket's upper bound (what this used to do) reports p50 as
    500ms when the true value is 83ms, because observations cluster at the
    bottom of a bucket.
    """
    if not bucket_list:
        return None
    total = bucket_list[-1][1]
    if not total:
        return None
    target = total * p
    prev_le, prev_cum = 0.0, 0.0
    for le, cum in bucket_list:
        if le == "+Inf":
            return None  # the residual bucket has no finite upper bound
        if cum >= target:
            width = cum - prev_cum
            if width <= 0:
                return round(float(le) * 1000, 1)
            frac = (target - prev_cum) / width
            return round((prev_le + (float(le) - prev_le) * frac) * 1000, 1)
        prev_le, prev_cum = float(le), cum
    return None


def aggregate_routes(samples) -> dict:
    """(method, route) -> {requests, errors}. 5xx only, so a 4xx route reads clean."""
    by_route: dict[tuple[str, str], dict[str, int]] = {}
    for s in samples:
        labels = s.labels or {}
        key = (labels.get("method", ""), labels.get("route", "unmatched"))
        entry = by_route.setdefault(key, {"requests": 0, "errors": 0})
        entry["requests"] += s.value
        if int(labels.get("status", 0) or 0) >= 500:
            entry["errors"] += s.value
    return by_route


def histogram_rows(samples) -> list[dict]:
    rows = []
    buckets: dict[tuple[str, str], list] = {}
    for s in samples:
        if not s.name.endswith("_bucket"):
            continue
        labels = s.labels or {}
        key = (labels.get("method", ""), labels.get("route", "unmatched"))
        buckets.setdefault(key, []).append((labels.get("le", ""), s.value))
    for (method, route), bucket_list in buckets.items():
        bucket_list.sort(key=lambda x: float(x[0]) if x[0] != "+Inf" else float("inf"))
        total = bucket_list[-1][1] if bucket_list else 0
        if not total:
            continue
        rows.append(
            {
                "method": method,
                "route": route,
                "requests": int(total),
                "p50_ms": percentile(bucket_list, 0.50),
                "p95_ms": percentile(bucket_list, 0.95),
            }
        )
    return sorted(rows, key=lambda r: r["requests"], reverse=True)


def metrics() -> None:
    sidebar()
    client = build_client()
    st.subheader("Metrics")
    st.caption("Parsed from `GET /metrics` (Prometheus).")

    # Render the button unconditionally: folding it into the `or` meant it was
    # never drawn on the first load, when the auto-fetch already fired.
    refresh = st.button("🔄 Refresh")
    if st.session_state.get("metrics") is None or refresh:
        submit("metrics", "GET", "/metrics", client=client, timeout=30)

    result = st.session_state.get("metrics")
    if result and result["err"] is not None:
        render_result("metrics")
        st.caption("If /metrics is disabled, set METRICS_ENABLED=true on the server.")
        return

    if not result:
        st.caption("No metrics loaded yet.")
        return

    families = {f.name: list(f.samples) for f in text_string_to_metric_families(result["resp"].text)}
    # prometheus_client exposes the counter under its family name, http_requests,
    # not the _total sample name.
    counter = families.get(COUNTER_FAMILY) or families.get(f"{COUNTER_FAMILY}_total") or []
    histogram = families.get(HISTOGRAM_FAMILY) or []

    if not counter and not histogram:
        st.info("No HTTP metrics found yet. Hit a few endpoints first.")
        return

    if counter:
        by_route = aggregate_routes(counter)
        total = sum(v["requests"] for v in by_route.values())
        errors = sum(v["errors"] for v in by_route.values())
        route_rows = [
            {
                "method": method,
                "route": route,
                "requests": int(v["requests"]),
                "error_rate_%": round(v["errors"] / v["requests"] * 100, 2) if v["requests"] else 0.0,
            }
            for (method, route), v in by_route.items()
        ]
        route_rows.sort(key=lambda r: r["requests"], reverse=True)

        m = st.columns(3)
        m[0].metric("Total requests", f"{int(total):,}")
        m[1].metric("Active routes", len(route_rows))
        m[2].metric("5xx share", f"{errors / total * 100:.2f}%" if total else "—")

        st.markdown("**Requests by route**")
        # Keyed by method + path: GET /pull and POST /pull are different ops.
        st.bar_chart(
            pd.DataFrame(route_rows).assign(
                label=lambda d: d["method"] + " " + d["route"]
            ).set_index("label")["requests"]
        )
        st.markdown("**5xx error rate by route**")
        st.dataframe(pd.DataFrame(route_rows), width="stretch", hide_index=True)

    if histogram:
        rows = histogram_rows(histogram)
        if rows:
            st.markdown("**Latency (ms)**")
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

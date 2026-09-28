"""Admin panel pure helpers.

These cover the four calculations that were silently wrong: histogram
percentile estimation, 5xx rate weighting, symbol parsing, and response
table selection. No Streamlit runtime needed — the helpers live in the page
modules, which only call st.* inside render().
"""

from collections import namedtuple

import pytest

from admin_panel.pages.metrics import aggregate_routes, histogram_rows, percentile
from admin_panel.pages.pulls import parse_symbols
from admin_panel.state import to_dataframe

# Mirrors prometheus_client's Sample: name, labels, value.
Sample = namedtuple("Sample", "name labels value")

BUCKETS = [("0.1", 3.0), ("0.5", 5.0), ("1.0", 5.0), ("2.5", 5.0), ("+Inf", 5.0)]


def _counter(method, route, status, value):
    return Sample("http_requests_total", {"method": method, "route": route, "status": status}, value)


def _bucket(method, route, le, value):
    return Sample(
        "http_request_duration_seconds_bucket",
        {"method": method, "route": route, "le": le},
        value,
    )


# --- percentile -------------------------------------------------------------
# The old code returned the bucket's upper bound, so p50 was reported as
# 500ms when the interpolated value is ~83ms.


def test_percentile_interpolates_inside_the_bucket():
    # 5 observations, all under 0.5s. p50 lands 2.5/3 of the way through the
    # first bucket: 0.1 * (2.5/3) = 0.0833s.
    assert percentile(BUCKETS, 0.50) == pytest.approx(83.3, abs=0.1)


def test_percentile_is_not_the_bucket_bound():
    assert percentile(BUCKETS, 0.95) == pytest.approx(450.0, abs=0.1)
    assert percentile(BUCKETS, 0.95) != 2500.0


def test_percentile_needs_a_finite_bound():
    assert percentile([("+Inf", 5.0)], 0.95) is None
    assert percentile([], 0.95) is None
    assert percentile([("0.1", 0.0), ("+Inf", 0.0)], 0.50) is None


# --- 5xx rate ---------------------------------------------------------------
# The old code summed per-route percentages, so a route with 1 error out of
# 2 requests contributed 50% to the headline figure regardless of its size.


def test_aggregate_routes_groups_by_method_and_route():
    samples = [
        _counter("GET", "/healthz", "200", 3),
        _counter("GET", "/healthz", "500", 1),
        _counter("POST", "/pull", "202", 2),
    ]
    assert aggregate_routes(samples) == {
        ("GET", "/healthz"): {"requests": 4, "errors": 1},
        ("POST", "/pull"): {"requests": 2, "errors": 0},
    }


def test_aggregate_routes_separates_methods_on_a_shared_path():
    # GET /pull and POST /pull are different operations and must not merge.
    samples = [_counter("GET", "/pull", "200", 5), _counter("POST", "/pull", "503", 1)]
    by_route = aggregate_routes(samples)
    assert by_route[("GET", "/pull")]["errors"] == 0
    assert by_route[("POST", "/pull")]["errors"] == 1


def test_aggregate_routes_falls_back_for_missing_labels():
    assert aggregate_routes([Sample("http_requests_total", {}, 7)]) == {
        ("", "unmatched"): {"requests": 7, "errors": 0}
    }


def test_five_xx_share_is_weighted_not_summed():
    # 7 errors out of 122 total = 5.7%. Summing the per-route rates gave
    # 0% (route a) + 25% (route b) + 100% (route c) = 125%.
    samples = [
        _counter("GET", "/a", "200", 100),
        _counter("GET", "/b", "200", 15),
        _counter("GET", "/b", "500", 5),
        _counter("GET", "/c", "503", 2),
    ]
    by_route = aggregate_routes(samples)
    total = sum(v["requests"] for v in by_route.values())
    errors = sum(v["errors"] for v in by_route.values())
    assert (total, errors) == (122, 7)
    assert round(errors / total * 100, 1) == 5.7


# --- histogram rows ---------------------------------------------------------


def test_histogram_rows_interpolates_and_ignores_sum_and_count():
    samples = [
        _bucket("GET", "/healthz", le, v) for le, v in BUCKETS
    ] + [
        Sample("http_request_duration_seconds_sum", {"method": "GET", "route": "/healthz"}, 1.2),
        Sample("http_request_duration_seconds_count", {"method": "GET", "route": "/healthz"}, 5.0),
    ]
    assert histogram_rows(samples) == [
        {
            "method": "GET",
            "route": "/healthz",
            "requests": 5,
            "p50_ms": pytest.approx(83.3, abs=0.1),
            "p95_ms": pytest.approx(450.0, abs=0.1),
        }
    ]


def test_histogram_rows_skips_a_route_with_no_traffic():
    samples = [_bucket("GET", "/cold", le, 0.0) for le in ("0.1", "+Inf")]
    assert histogram_rows(samples) == []


# --- symbol parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("RELIANCE", ["RELIANCE"]),
        ("TCS, INFY\nHDFCBANK", ["HDFCBANK", "INFY", "TCS"]),
        ("reliance reliance  tcs", ["RELIANCE", "TCS"]),
        ("M&M BAJAJ-AUTO", ["BAJAJ-AUTO", "M&M"]),
        ("   ", []),
    ],
)
def test_parse_symbols(text, expected):
    assert parse_symbols(text) == expected


# --- response table selection -----------------------------------------------


def test_to_dataframe_returns_none_for_an_all_scalar_response():
    assert to_dataframe({"symbol": "TCS", "pe_ratio": 30.2, "roe": 41.0}) is None


def test_to_dataframe_finds_the_list_of_objects():
    df = to_dataframe({"rows": [{"a": 1}, {"a": 2}]})
    assert list(df.columns) == ["a"] and len(df) == 2


def test_to_dataframe_accepts_a_bare_list():
    assert list(to_dataframe([{"a": 1}]).columns) == ["a"]


def test_to_dataframe_returns_none_for_empty_and_scalar_lists():
    assert to_dataframe({"sources": ["nse", "sec"]}) is None
    assert to_dataframe({}) is None
    assert to_dataframe([]) is None

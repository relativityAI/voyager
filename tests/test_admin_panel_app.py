"""End-to-end Streamlit behaviour for the admin panel.

The two bugs these guard against were both caused by rendering a result out
of the button branch that produced it: Streamlit re-runs the page on every
interaction, so the next keystroke deleted the response on screen — including
a freshly created API key, which the server will never show again.
"""

import pytest
from streamlit.testing.v1 import AppTest

from admin_panel.client import PanelHTTPError, Response

HARNESS = """
import os
os.environ["VOYAGER_PANEL_CONFIG_DIR"] = {config_dir!r}
import streamlit as st
from admin_panel.state import init_state
from admin_panel.pages.{module} import {module}
st.set_page_config(page_title="t", layout="wide")
init_state()
{module}()
"""


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Never read or write the real ~/.config/voyager/panel.json."""
    monkeypatch.setenv("VOYAGER_PANEL_CONFIG_DIR", str(tmp_path / "empty"))
    for var in ("VOYAGER_API_KEY", "VOYAGER_ADMIN_KEY", "DATABASE_URL"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("VOYAGER_BASE_URL", "http://panel.test")


def _app(tmp_path, module, monkeypatch, responses):
    """Drive a page with VoyagerClient.request replaced by a canned responder.

    The sidebar health pill probes /healthz on every render; answer it for
    free so it doesn't consume the queue the test is asserting on.
    """
    calls = []

    def fake_request(self, method, path, params=None, json=None, admin=False,
                     timeout=120, ok_status=(200, 201, 202), retries=None):
        calls.append((method, path, params, json, admin, retries))
        # Background refreshes, not the subject under test: answer for free so
        # they don't consume the queue a test is asserting on.
        if path == "/healthz":
            return _ok({"ok": True})
        if method == "GET" and path == "/admin/keys":
            return _ok([])
        if not responses:
            raise AssertionError(f"unexpected {method} {path}")
        outcome = responses.pop(0)
        if isinstance(outcome, PanelHTTPError):
            raise outcome
        return outcome

    monkeypatch.setattr("admin_panel.client.VoyagerClient.request", fake_request)
    script = HARNESS.format(config_dir=str(tmp_path / "empty"), module=module)
    return AppTest.from_string(script, default_timeout=30), calls


def _with_admin_key(monkeypatch):
    monkeypatch.setenv("VOYAGER_ADMIN_KEY", "admin-test-key")


def _ok(payload, status=200):
    return Response(
        status_code=status,
        json=payload,
        text="",
        elapsed_ms=12.3,
        headers={},
    )


# --- the lost-response bug ---------------------------------------------------


def test_playground_response_survives_a_later_interaction(tmp_path, monkeypatch):
    at, _ = _app(
        tmp_path, "playground", monkeypatch,
        [_ok({"rows": [{"a": 1, "b": 2}]}, status=200)],
    )
    at.run()
    at.button[0].click().run()  # ▶️ Execute
    assert not at.exception
    assert at.json or at.dataframe, "response was not rendered after Execute"

    # Interact again. Before the fix this rerun dropped the response entirely.
    at.radio[0].set_value("Raw request").run()
    assert not at.exception
    assert at.json or at.dataframe, "response was destroyed by an unrelated edit"
    assert any("Last result" in c.value for c in at.caption)


def test_playground_history_records_the_request(tmp_path, monkeypatch):
    at, calls = _app(tmp_path, "playground", monkeypatch, [_ok({"rows": [{"a": 1}]})])
    at.run()
    at.button[0].click().run()
    assert len(calls) == 1
    assert at.dataframe, "history table missing"


# --- the lost-API-key bug ----------------------------------------------------


def test_created_key_stays_until_dismissed(tmp_path, monkeypatch):
    _with_admin_key(monkeypatch)
    at, calls = _app(
        tmp_path, "keys", monkeypatch,
        [_ok({"key": "vgr_secretvalue", "label": None, "created_at": "2026-01-01"})],
    )
    at.run()
    at.text_input(key="key_name").set_value("my-app").run()
    at.button[0].click().run()  # ➕ Create key
    assert not at.exception
    assert any("vgr_secretvalue" in c.value for c in at.code), "raw key not shown"

    # Any other interaction must not lose it.
    at.text_input(key="key_owner").set_value("team").run()
    assert any("vgr_secretvalue" in c.value for c in at.code), (
        "raw API key was destroyed before it could be copied"
    )

    # POST /admin/keys must never be retried: the server already created it.
    # None is fine — it means the page didn't ask, so the client's
    # method-aware default (0 for writes) applies.
    posts = [c for c in calls if c[0] == "POST"]
    assert len(posts) == 1, f"expected exactly one create, got {len(posts)}"
    assert posts[0][5] in (None, 0), "key creation was sent with retries enabled"


def test_revoked_key_list_reloads_only_on_demand(tmp_path, monkeypatch):
    _with_admin_key(monkeypatch)
    at, calls = _app(
        tmp_path, "keys", monkeypatch,
        [_ok([{"name": "a", "prefix": "vgr_aaaaaaaaaaaa", "scopes": ["data:read"],
               "rpm": 60, "enabled": True, "expires_at": None, "last_used_at": None,
               "created_at": "2026-01-01", "owner": "", "revoked_at": None}])],
    )
    at.run()
    assert len(calls) == 1, "expected a single admin call on first render"

    # Rerunning with no interaction must not re-issue the admin call.
    at.run()
    assert len(calls) == 1, "GET /admin/keys fired again on a bare rerun"


# --- retries ----------------------------------------------------------------


def test_post_is_never_retried_but_get_is(monkeypatch):
    """Retries are only safe for reads; replaying a POST creates duplicate keys."""
    import requests

    from admin_panel.client import VoyagerClient

    seen = []

    def fake_session_request(*a, **kw):
        seen.append(a[0])
        raise requests.ConnectionError("cold start")

    client = VoyagerClient("http://panel.test", "k", "")
    client.session.request = fake_session_request
    monkeypatch.setattr("admin_panel.client.time.sleep", lambda _s: None)

    for method, expected in (("GET", 4), ("POST", 1), ("DELETE", 1)):
        seen.clear()
        with pytest.raises(PanelHTTPError):
            client.request(method, "/pull", retries=None)
        assert len(seen) == expected, (
            f"{method} made {len(seen)} attempts, want {expected}"
        )


# --- metrics -----------------------------------------------------------------


def test_metrics_renders_the_counter_block(tmp_path, monkeypatch):
    exposition = (
        "# HELP http_requests_total Total HTTP requests served\n"
        "# TYPE http_requests_total counter\n"
        'http_requests_total{method="GET",route="/healthz",status="200"} 9.0\n'
        'http_requests_total{method="GET",route="/healthz",status="500"} 1.0\n'
        "# HELP http_request_duration_seconds HTTP request duration in seconds\n"
        "# TYPE http_request_duration_seconds histogram\n"
        'http_request_duration_seconds_bucket{method="GET",route="/healthz",le="0.1"} 8.0\n'
        'http_request_duration_seconds_bucket{method="GET",route="/healthz",le="+Inf"} 10.0\n'
        'http_request_duration_seconds_sum{method="GET",route="/healthz"} 1.0\n'
        'http_request_duration_seconds_count{method="GET",route="/healthz"} 10.0\n'
    )
    at, _ = _app(
        tmp_path, "metrics", monkeypatch,
        [Response(200, None, exposition, 5.0, {})],
    )
    at.run()
    assert not at.exception
    labels = {m.label for m in at.metric}
    assert "Total requests" in labels, f"counter block missing; saw {labels}"
    assert "5xx share" in labels
    # 1 error out of 10 requests, weighted — the old sum-of-rates read 10%.
    assert {m.label: m.value for m in at.metric}["5xx share"] == "10.00%"


# --- auth hint ---------------------------------------------------------------


def test_job_list_403_names_the_missing_scope(tmp_path, monkeypatch):
    at, _ = _app(
        tmp_path, "pulls", monkeypatch,
        [PanelHTTPError(403, "This key requires the 'data:write' scope")],
    )
    at.run()
    at.toggle[0].set_value(True).run()
    # The hint must key off status_code; the detail string never contains "403".
    assert any("data:write" in w.value for w in at.warning), (
        "403 hint did not fire"
    )

"""Agent-grade contract tests: OpenAPI discoverability, RFC 9457 errors, llms.txt.

These are the regression guard for the agent-friendliness work: if a future
edit drops auth from the spec, un-types a response, or reverts errors to the
old prose shapes, this file fails.
"""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import _RESPONSE_MODELS, app
from src.auth import get_current_api_key


@pytest.fixture
def plain_client():
    """Real auth path (no conftest override) — but always without a key."""
    overridden = app.dependency_overrides.pop(get_current_api_key, None)
    client = TestClient(app)
    yield client
    if overridden is not None:
        app.dependency_overrides[get_current_api_key] = overridden


def _ops():
    schema = app.openapi()
    for path, item in schema["paths"].items():
        for method, op in item.items():
            if isinstance(op, dict):
                yield path, method, op


# --- spec discoverability ----------------------------------------------------


def test_security_schemes_declared():
    schemes = app.openapi()["components"]["securitySchemes"]
    assert schemes["ApiKeyHeader"]["name"] == "X-API-Key"
    assert schemes["ApiKeyHeader"]["in"] == "header"
    assert schemes["BearerAuth"]["scheme"] == "bearer"
    assert schemes["AdminHeader"]["name"] == "X-Voyager-Admin-Key"


def test_every_operation_declares_security():
    public = {"/", "/healthz", "/readyz", "/metrics", "/llms.txt"}
    for path, method, op in _ops():
        if path in public:
            assert "security" not in op, f"{method} {path} should be public"
        else:
            assert op.get("security"), f"{method} {path} has no security"


def test_every_operation_has_typed_success_response():
    for path, method, op in _ops():
        for code in ("200", "201", "202"):
            if code in op.get("responses", {}):
                schema = (
                    op["responses"][code]
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                assert schema, f"{method} {path} {code} has no response schema"
                break
        else:
            raise AssertionError(f"{method} {path} has no success response")


def test_response_model_map_matches_live_routes():
    """Drift guard: every _RESPONSE_MODELS key must be a route the app serves."""
    live = {
        (m.lower(), r.path)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods
    }
    stale = {
        k for k in _RESPONSE_MODELS if (k[0].lower(), k[1]) not in live
    }
    assert not stale, f"stale _RESPONSE_MODELS keys: {stale}"


def test_enums_are_in_the_spec():
    schema = app.openapi()

    def param(path, method, name):
        return next(
            p
            for p in schema["paths"][path][method]["parameters"]
            if p["name"] == name
        )

    assert param("/financials", "get", "filing_type")["schema"]["enum"] == [
        "quarterly",
        "annual",
    ]
    assert param("/financial-metrics", "get", "filing_type")["schema"]["enum"] == [
        "quarterly",
        "annual",
        "ttm",
    ]
    assert param("/list", "get", "category")["schema"]["enum"] == [
        "sources",
        "countries",
        "industries",
        "sectors",
        "indices",
    ]
    assert param("/history", "get", "period")["schema"]["enum"] == [
        "3mo",
        "6mo",
        "1y",
        "2y",
        "5y",
        "max",
    ]
    assert param("/announcements", "get", "market")["schema"]["enum"] == [
        "equities",
        "sme",
    ]


def test_error_responses_advertise_the_problem_schema():
    for path, method, op in _ops():
        for code, resp in op.get("responses", {}).items():
            if str(code)[0] in "45":
                content = resp.get("content", {})
                assert "application/problem+json" in content, (
                    f"{method} {path} {code} is not problem+json"
                )


# --- runtime error shapes ----------------------------------------------------


def test_401_is_rfc9457_with_string_detail(plain_client):
    resp = plain_client.get("/financial-metrics", params={"symbol": "VBL"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["code"] == "missing_api_key"
    assert body["status"] == 401
    assert body["retry"] is False
    assert body["type"] == "/problems/missing_api_key"


def test_422_lists_bad_fields(client):
    resp = client.get(
        "/financials", params={"symbol": "VBL", "filing_type": "foo"}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["detail"], str) and "filing_type" in body["detail"]
    assert body["errors"]
    assert body["retry"] is False


def test_service_error_501_is_problem_shaped(client):
    resp = client.get("/list", params={"category": "sources", "source": "bogus"})
    assert resp.status_code == 501
    body = resp.json()
    assert body["code"] == "unsupported_source"
    assert body["status"] == 501
    assert isinstance(body["detail"], str) and "not yet supported" in body["detail"]
    assert body["retry"] is False


def test_404_job_is_problem_shaped(client, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("api.get_job", AsyncMock(return_value=None))
    resp = client.get("/pull/jobs/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "not_found"
    assert isinstance(body["detail"], str)


# --- discovery ---------------------------------------------------------------


def test_llms_txt_served(client):
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "/openapi.json" in resp.text
    assert "X-API-Key" in resp.text
    assert "problem+json" in resp.text


def test_root_unchanged():
    resp = TestClient(app).get("/")
    assert resp.json() == {"ok": 1}

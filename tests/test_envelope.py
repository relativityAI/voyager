"""Tests for Phase 1 response envelope middleware."""

import pytest
from fastapi.testclient import TestClient

from api import app
from src.auth import get_current_api_key


@pytest.fixture(scope="session")
def plain_client():
    return TestClient(app)


@pytest.fixture
def no_auth_override():
    overridden = app.dependency_overrides.pop(get_current_api_key, None)
    yield
    if overridden is not None:
        app.dependency_overrides[get_current_api_key] = overridden


def test_no_envelope_by_default(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True}
    assert "data" not in body
    assert "meta" not in body


def test_envelope_via_accept_header(client):
    resp = client.get(
        "/healthz",
        headers={"Accept": "application/vnd.voyager.envelope+json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert body["data"] == {"ok": True}
    assert "request_id" in body["meta"]
    assert "timestamp" in body["meta"]


def test_envelope_via_query_param(client):
    resp = client.get("/healthz?envelope=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {"ok": True}
    assert "request_id" in body["meta"]


def test_error_not_wrapped(plain_client, no_auth_override):
    resp = plain_client.get("/healthz", headers={"Accept": "application/vnd.voyager.envelope+json"})
    # healthz is public, so it succeeds — but let's test an error path
    resp = plain_client.get("/financial-metrics", params={"symbol": "VBL"}, headers={"Accept": "application/vnd.voyager.envelope+json"})
    assert resp.status_code == 401
    body = resp.json()
    assert "data" not in body
    assert "type" in body
    assert body["type"] == "/problems/missing_api_key"


def test_envelope_skips_non_json(client):
    resp = client.get(
        "/llms.txt",
        headers={"Accept": "application/vnd.voyager.envelope+json"},
    )
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers.get("content-type", "")


def test_envelope_no_double_wrap(client):
    resp = client.get(
        "/healthz?envelope=true",
        headers={"Accept": "application/vnd.voyager.envelope+json"},
    )
    body = resp.json()
    assert body["data"] == {"ok": True}
    assert isinstance(body["meta"], dict)

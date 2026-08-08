"""Tests for public health/metrics endpoints and auth enforcement at the HTTP layer."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api import app
from src.auth import get_current_api_key


@pytest.fixture(scope="session")
def plain_client():
    """A TestClient without the auth dependency override (real auth path)."""
    return TestClient(app)


@pytest.fixture
def no_auth_override():
    """Temporarily remove the conftest auth override so real 401 paths run."""
    overridden = app.dependency_overrides.pop(get_current_api_key, None)
    yield
    if overridden is not None:
        app.dependency_overrides[get_current_api_key] = overridden


def test_root_health():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"ok": 1}


def test_healthz():
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_readyz_ok():
    with patch("api.ping_database", new=AsyncMock(return_value=True)):
        client = TestClient(app)
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_readyz_db_down():
    with patch("api.ping_database", new=AsyncMock(return_value=False)):
        client = TestClient(app)
        resp = client.get("/readyz")
    assert resp.status_code == 503


def test_metrics_exposed():
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "http_requests_total" in resp.text


def test_protected_endpoint_requires_key(plain_client, no_auth_override):
    resp = plain_client.get("/financial-metrics", params={"symbol": "VBL"})
    assert resp.status_code == 401


def test_admin_keys_requires_admin_header(plain_client, no_auth_override):
    resp = plain_client.get("/admin/keys")
    assert resp.status_code == 401


def test_admin_keys_wrong_admin_header(plain_client, no_auth_override):
    resp = plain_client.get("/admin/keys", headers={"X-Voyager-Admin-Key": "wrong"})
    assert resp.status_code == 401


def test_keyed_endpoint_works_with_valid_key(client):
    resp = client.get("/funds")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_implemented"

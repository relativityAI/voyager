"""Tests for Phase 2 agent_action on problem responses."""

import pytest
from fastapi.testclient import TestClient

from api import app, _STATUS_ACTION
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


def test_agent_action_on_401(plain_client, no_auth_override):
    resp = plain_client.get("/financial-metrics", params={"symbol": "VBL"})
    assert resp.status_code == 401
    assert resp.json()["agent_action"] == "authenticate"


def test_agent_action_on_404():
    from api import _problem
    resp = _problem(404, "Not Found")
    assert resp.status_code == 404
    import json
    body = json.loads(resp.body)
    assert body["agent_action"] == "abort_and_report"


def test_agent_action_on_422(plain_client):
    resp = plain_client.get("/search-categories", params={"category": "bogus", "source": "nse"})
    assert resp.status_code == 422
    assert resp.json()["agent_action"] == "reduce_scope"


def test_status_action_map_covers_all_status_codes():
    for status in _STATUS_ACTION:
        assert _STATUS_ACTION[status] in {
            "retry_with_backoff",
            "abort_and_report",
            "refresh_and_retry",
            "reduce_scope",
            "authenticate",
        }

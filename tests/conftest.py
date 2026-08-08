"""Shared test fixtures.

Overrides API-key auth so endpoint tests don't need a live MongoDB. All
`Depends(get_current_api_key)` uses (require_api_key, require_scope, ...)
resolve through this override.
"""

import os

os.environ.setdefault("VOYAGER_ADMIN_KEY", "test-admin-key")

import pytest
from fastapi.testclient import TestClient

from api import app
from src.auth import get_current_api_key


class _FakeKey:
    prefix = "vgr_test0000"
    name = "test"
    owner = "tests"
    scopes = ["data:read", "data:write"]
    rpm = 10000
    enabled = True
    revoked_at = None
    is_revoked = False
    is_expired = False


async def _fake_get_current_api_key():
    return _FakeKey()


app.dependency_overrides[get_current_api_key] = _fake_get_current_api_key


@pytest.fixture(scope="session")
def client():
    return TestClient(app)

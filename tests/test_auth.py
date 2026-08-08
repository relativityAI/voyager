"""Tests for API-key auth: admin key guard, key resolution, scope checks."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.auth.models import APIKey, generate_api_key, hash_key
from src.auth.security import get_current_api_key, require_admin_key, require_scope


class _StubKey:
    def __init__(self, enabled=True, revoked_at=None, is_expired=False, scopes=None):
        self.prefix = "vgr_test0000"
        self.name = "test"
        self.enabled = enabled
        self.revoked_at = revoked_at
        self.is_revoked = revoked_at is not None
        self.is_expired = is_expired
        self.scopes = scopes or ["data:read"]
        self.rpm = 1000

    async def set(self, *_a, **_k):
        return None


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------


def test_generate_api_key_prefix():
    raw = generate_api_key()
    assert raw.startswith("vgr_")
    assert len(raw) > 20


def test_hash_is_deterministic_and_irreversible():
    raw = generate_api_key()
    assert hash_key(raw) == hash_key(raw)
    assert hash_key(raw) != raw


def test_public_dict_never_leaks_hash():
    raw = generate_api_key()
    key = APIKey.model_construct(
        name="svc",
        prefix=raw[:12],
        key_hash=hash_key(raw),
        scopes=["data:read"],
        rpm=60,
    )
    pub = key.to_public_dict()
    assert pub["prefix"] == raw[:12]
    assert "key_hash" not in pub
    assert "hash" not in pub
    assert raw not in str(pub)


def test_is_revoked_and_expired():
    from src.utils.helpers import utcnow

    revoked = _StubKey(revoked_at=utcnow())
    assert revoked.is_revoked
    assert not revoked.is_expired

    expired = _StubKey(is_expired=True)
    assert expired.is_expired


# ---------------------------------------------------------------------------
# require_admin_key
# ---------------------------------------------------------------------------


def test_admin_key_requires_env(monkeypatch):
    monkeypatch.delenv("VOYAGER_ADMIN_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_admin_key(x_voyager_admin_key="x")
    assert exc.value.status_code == 503


def test_admin_key_rejects_wrong(monkeypatch):
    monkeypatch.setenv("VOYAGER_ADMIN_KEY", "secret")
    with pytest.raises(HTTPException) as exc:
        require_admin_key(x_voyager_admin_key="wrong")
    assert exc.value.status_code == 401


def test_admin_key_accepts(monkeypatch):
    monkeypatch.setenv("VOYAGER_ADMIN_KEY", "secret")
    assert require_admin_key(x_voyager_admin_key="secret") == "secret"


def test_admin_key_rejects_missing_header(monkeypatch):
    monkeypatch.setenv("VOYAGER_ADMIN_KEY", "secret")
    with pytest.raises(HTTPException) as exc:
        require_admin_key(x_voyager_admin_key=None)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# get_current_api_key
# ---------------------------------------------------------------------------


@patch("src.auth.security.check_rate_limit", new=AsyncMock(return_value=True))
async def _resolve(header=None, bearer=None, find_result=None):
    with patch(
        "src.auth.security.APIKey.find_by_key", new=AsyncMock(return_value=find_result)
    ):
        return await get_current_api_key(x_api_key=header, authorization=bearer)


def test_api_key_missing_header():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_resolve(header=None))
    assert exc.value.status_code == 401


def test_api_key_bearer_accepted():
    key = _StubKey()
    result = asyncio.run(
        _resolve(bearer=f"Bearer {generate_api_key()}", find_result=key)
    )
    assert result is key


def test_api_key_unknown_rejected():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_resolve(header=generate_api_key(), find_result=None))
    assert exc.value.status_code == 401


def test_api_key_disabled_rejected():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_resolve(header="k", find_result=_StubKey(enabled=False)))
    assert exc.value.status_code == 401


def test_api_key_revoked_rejected():
    from src.utils.helpers import utcnow

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_resolve(header="k", find_result=_StubKey(revoked_at=utcnow())))
    assert exc.value.status_code == 401


def test_api_key_expired_rejected():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_resolve(header="k", find_result=_StubKey(is_expired=True)))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# require_scope
# ---------------------------------------------------------------------------


def test_require_scope_allows_matching_scope():
    dep = require_scope("data:write")
    key = _StubKey(scopes=["data:read", "data:write"])
    assert asyncio.run(dep(key=key)) is key


def test_require_scope_rejects_missing():
    dep = require_scope("data:write")
    key = _StubKey(scopes=["data:read"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(key=key))
    assert exc.value.status_code == 403
    assert "data:write" in str(exc.value.detail)

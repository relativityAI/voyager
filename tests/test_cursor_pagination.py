"""Tests for Phase 3 cursor pagination on /announcements."""

import pytest
from fastapi.testclient import TestClient

from api import app, _encode_cursor, _decode_cursor


@pytest.fixture(scope="session")
def plain_client():
    return TestClient(app)


def test_cursor_encode_decode_roundtrip():
    assert _decode_cursor(_encode_cursor(42)) == 42
    assert _decode_cursor(_encode_cursor(0)) == 0


def test_decode_invalid_cursor_returns_zero():
    assert _decode_cursor("not-valid!!!") == 0


def test_cursor_pagination_with_mock(monkeypatch):
    import api as api_mod

    async def fake_get_announcements(symbol, country, source, market, limit=None, offset=0):
        all_items = [{"date": f"2026-01-{i+1:02d}", "heading": f"Ann {i}"} for i in range(10)]
        items = all_items[offset:offset + limit] if limit else all_items[offset:]
        return {"symbol": symbol, "source": source, "market": market, "announcements": items}

    monkeypatch.setattr(api_mod, "get_announcements", fake_get_announcements)
    client = TestClient(app)

    resp = client.get("/announcements", params={"symbol": "X", "cursor": _encode_cursor(0), "limit": 3})
    body = resp.json()
    assert len(body["data"]) == 3
    assert body["meta"]["has_next"] is True
    assert body["meta"]["next_cursor"] == _encode_cursor(3)

    resp2 = client.get("/announcements", params={"symbol": "X", "cursor": body["meta"]["next_cursor"], "limit": 3})
    body2 = resp2.json()
    assert body2["data"][0]["heading"] == "Ann 3"
    assert body2["meta"]["has_next"] is True

    resp3 = client.get("/announcements", params={"symbol": "X", "cursor": _encode_cursor(9), "limit": 3})
    body3 = resp3.json()
    assert len(body3["data"]) == 1
    assert body3["meta"]["has_next"] is False
    assert body3["meta"]["next_cursor"] is None

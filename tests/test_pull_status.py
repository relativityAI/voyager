import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.nse.client import CookieError

try:
    from api import pull_nse_data
except ImportError:
    pytest.skip("Skipping pull status tests: api import unavailable (motor/pymongo issue)", allow_module_level=True)


class _OkResponse:
    def __init__(self, data):
        self.data = data

    @property
    def status_code(self):
        return 200

    def json(self):
        return self.data


def _fake_meta_cls():
    cls = MagicMock()
    cls.find_one = AsyncMock(return_value=None)
    instance = MagicMock()
    instance.insert = AsyncMock()
    cls.return_value = instance
    return cls


def test_pull_status_failed_when_all_endpoints_cookie_fail():
    with patch("api.get_database", return_value=MagicMock()), patch(
        "api.nse_scraper.api._call", side_effect=CookieError("no cookies")
    ):
        result = asyncio.run(pull_nse_data("TEST"))

    assert result["status"] == "failed"
    assert result["records_pulled"] == 0
    assert all(v == "cookie failed" for v in result["endpoint_breakdown"].values())


def test_pull_status_partial_when_some_endpoints_cookie_fail():
    def fake_call(url, symbol=None, **kwargs):
        if "corp-info" in url:
            return _OkResponse({"data": [{"a": 1}]})
        if "event-calendar" in url:
            return _OkResponse([{"e": 2}])
        raise CookieError("no cookies")

    with patch("api.get_database", return_value=MagicMock()), patch(
        "api.NSEStockMetadata", _fake_meta_cls()
    ), patch("api.nse_scraper.api._call", side_effect=fake_call):
        result = asyncio.run(pull_nse_data("TEST"))

    assert result["status"] == "partial"
    assert result["records_pulled"] == 2
    assert any(v == "cookie failed" for v in result["endpoint_breakdown"].values())


def test_pull_status_completed_without_cookie_failures():
    def fake_call(url, symbol=None, **kwargs):
        if "corp-info" in url:
            return _OkResponse({"data": [{"a": 1}]})
        return _OkResponse({})

    with patch("api.get_database", return_value=MagicMock()), patch(
        "api.NSEStockMetadata", _fake_meta_cls()
    ), patch("api.nse_scraper.api._call", side_effect=fake_call):
        result = asyncio.run(pull_nse_data("TEST"))

    assert result["status"] == "completed"
    assert "cookie failed" not in result["endpoint_breakdown"].values()


def _pull_with_existing_urls(existing_urls, refresh=False):
    processed = {"n": 0}

    def fake_call(url, symbol=None, **kwargs):
        return _OkResponse({"data": [{"xbrl": "http://x/one.xml", "consolidated": "Consolidated"}]})

    def fake_process(x, symbol, category):
        processed["n"] += 1
        return {
            "income_statement": {
                "symbol": symbol,
                "period_end_date": "2025-09-30",
                "consolidated": True,
                "source_endpoint": category,
            },
            "balance_sheet": None,
            "cash_flow": None,
            "shareholding": None,
        }

    class FakeCursor:
        def __init__(self, docs):
            self._docs = list(docs)
            self._it = None

        def __aiter__(self):
            self._it = iter(self._docs)
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class FakeColl:
        def find(self, filt, projection=None):
            return FakeCursor(
                [{"xbrl_url": u} for u in existing_urls] if filt.get("symbol") == "TEST" else []
            )

        async def bulk_write(self, ops, ordered=False):
            return MagicMock()

    class FakeDB:
        def __getitem__(self, name):
            return FakeColl()

    with patch("api.get_database", return_value=FakeDB()), patch(
        "api.NSEStockMetadata", _fake_meta_cls()
    ), patch("api.nse_scraper.api._call", side_effect=fake_call), patch(
        "api.nse_scraper.process_xbrl", side_effect=fake_process
    ):
        result = asyncio.run(pull_nse_data("TEST", "quarterly", refresh=refresh))

    return result, processed["n"]


def test_pull_skips_existing_xbrl_before_download():
    result, processed = _pull_with_existing_urls(["http://x/one.xml"])
    assert processed == 0
    assert result["timing"]["counts"].get("skipped_existing", 0) >= 1
    assert result["status"] == "completed"


def test_pull_refresh_reprocesses_existing_xbrl():
    result, processed = _pull_with_existing_urls(["http://x/one.xml"], refresh=True)
    assert processed >= 1
    assert result["timing"]["counts"].get("skipped_existing", 0) == 0

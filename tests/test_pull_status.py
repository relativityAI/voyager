import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.nse.client import CookieError

try:
    from src.services.nse import pull_nse_data
except ImportError:
    pytest.skip(
        "Skipping pull status tests: api import unavailable",
        allow_module_level=True,
    )


class _OkResponse:
    def __init__(self, data):
        self.data = data

    @property
    def status_code(self):
        return 200

    def json(self):
        return self.data


def _make_session_cm(mock_session):
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__exit__ = AsyncMock(return_value=False)
    return mock_cm


def _mock_factory(mock_session):
    cm = _make_session_cm(mock_session)
    factory = MagicMock(return_value=cm)
    return factory


def _make_execute_mock(urls=None):
    """Return an AsyncMock for session.execute that returns scalars().all() = urls."""
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = urls or []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    return AsyncMock(return_value=mock_result)


def _make_session(urls=None):
    """Create a mock async session with execute and commit wired up."""
    session = AsyncMock()
    session.execute = _make_execute_mock(urls)
    return session


def test_pull_status_failed_when_all_endpoints_cookie_fail():
    mock_session = _make_session()
    factory = _mock_factory(mock_session)
    with (
        patch("src.services.nse.get_session_factory", return_value=factory),
        patch(
            "src.services.nse.nse_scraper.api._call",
            side_effect=CookieError("no cookies"),
        ),
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

    mock_session = _make_session()
    factory = _mock_factory(mock_session)
    with (
        patch("src.services.nse.get_session_factory", return_value=factory),
        patch("src.services.nse.nse_scraper.api._call", side_effect=fake_call),
    ):
        result = asyncio.run(pull_nse_data("TEST"))

    assert result["status"] == "partial"
    assert result["records_pulled"] == 2
    assert any(v == "cookie failed" for v in result["endpoint_breakdown"].values())


def test_pull_status_completed_without_cookie_failures():
    def fake_call(url, symbol=None, **kwargs):
        if "corp-info" in url:
            return _OkResponse({"data": [{"a": 1}]})
        return _OkResponse({})

    mock_session = _make_session()
    factory = _mock_factory(mock_session)
    with (
        patch("src.services.nse.get_session_factory", return_value=factory),
        patch("src.services.nse.nse_scraper.api._call", side_effect=fake_call),
    ):
        result = asyncio.run(pull_nse_data("TEST"))

    assert result["status"] == "completed"
    assert "cookie failed" not in result["endpoint_breakdown"].values()


def _pull_with_existing_urls(existing_urls, refresh=False):
    processed = {"n": 0}

    def fake_call(url, symbol=None, **kwargs):
        return _OkResponse(
            {"data": [{"xbrl": "http://x/one.xml", "consolidated": "Consolidated"}]}
        )

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

    mock_session = _make_session(
        list(existing_urls) if existing_urls else []
    )
    factory = _mock_factory(mock_session)
    with (
        patch("src.services.nse.get_session_factory", return_value=factory),
        patch("src.services.nse.nse_scraper.api._call", side_effect=fake_call),
        patch("src.services.nse.nse_scraper.process_xbrl", side_effect=fake_process),
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

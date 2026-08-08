import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client

import mcp_server
from src.services._common import NotFoundError


@pytest.fixture
def mock_db():
    with patch.object(mcp_server, "init_db", new=AsyncMock()):
        yield


def _call_tool(name: str, arguments: dict, *, raise_on_error: bool = True):
    """Run a tool call through the in-memory MCP client."""

    async def run():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(
                name, arguments, raise_on_error=raise_on_error
            )

    return asyncio.run(run())


def test_tool_registration(mock_db):
    async def run():
        async with Client(mcp_server.mcp) as client:
            tools = await client.list_tools()
            return {t.name for t in tools}

    names = asyncio.run(run())
    expected = {
        "ping",
        "version",
        "list_categories",
        "get_financials",
        "get_income_statements",
        "get_balance_sheets",
        "get_cash_flows",
        "pull_status",
        "get_financial_metrics",
        "announcements",
        "shareholdings",
        "nse_financials_raw",
        "nse_announcements",
        "nse_announcements_search",
        "nse_announcements_extract",
        "nse_annual_reports_list",
        "nse_annual_reports",
        "nse_shareholdings_raw",
        "get_source_schema",
        "nse_full_download",
        "screener_fetch",
        "screener_screen",
        "trendlyne_fetch",
        "stockscans_fetch",
        "marketsmithindia_fetch",
    }
    assert expected.issubset(names)


def test_ping(mock_db):
    res = _call_tool("ping", {})
    data = json.loads(res.content[0].text)
    assert data["ok"] == 1
    assert data["name"] == "Voyager"


def test_version(mock_db):
    res = _call_tool("version", {})
    data = json.loads(res.content[0].text)
    assert data["version"] == mcp_server.__version__


def test_list_categories(mock_db):
    sample = {
        "category": "sources",
        "country": "in",
        "source": "NSE",
        "data": [{"name": "NSE", "type": "exchange"}],
    }
    with patch.object(mcp_server, "list_category", return_value=sample):
        res = _call_tool("list_categories", {"category": "sources"})
    data = json.loads(res.content[0].text)
    assert data["category"] == "sources"
    assert data["data"][0]["name"] == "NSE"


def test_financial_metrics(mock_db):
    sample = {"symbol": "VBL", "current_price": 442.3, "revenue_growth": 14.64}
    with patch.object(mcp_server, "get_metrics", new=AsyncMock(return_value=sample)):
        res = _call_tool(
            "get_financial_metrics", {"symbol": "VBL", "filing_type": "ttm"}
        )
    data = json.loads(res.content[0].text)
    assert data["symbol"] == "VBL"
    assert data["revenue_growth"] == 14.64


def test_income_statements(mock_db):
    sample = {"income_statements": [{"symbol": "VBL", "revenue": 100.0}]}
    with patch.object(
        mcp_server, "get_statement_data", new=AsyncMock(return_value=sample)
    ):
        res = _call_tool("get_income_statements", {"symbol": "VBL", "limit": 2})
    data = json.loads(res.content[0].text)
    assert data["income_statements"][0]["symbol"] == "VBL"


def test_pull_status_serializes_datetimes(mock_db):
    sample = {
        "symbol": "VBL",
        "source": "NSE",
        "last_pull": datetime(2026, 1, 1, 12, 30, 0),
        "available": True,
    }
    with patch.object(
        mcp_server, "get_pull_status", new=AsyncMock(return_value=sample)
    ):
        res = _call_tool("pull_status", {"symbol": "VBL"})
    data = json.loads(res.content[0].text)
    assert data["last_pull"] == "2026-01-01T12:30:00"


def test_service_error_surfaces(mock_db):
    with patch.object(
        mcp_server,
        "get_pull_status",
        new=AsyncMock(side_effect=NotFoundError("No data found for VBL")),
    ):
        res = _call_tool("pull_status", {"symbol": "VBL"}, raise_on_error=False)
    assert res.is_error
    text = "".join(c.text for c in res.content if getattr(c, "text", None))
    assert "NotFoundError" in text
    assert "No data found for VBL" in text


def test_get_source_schema(mock_db):
    res = _call_tool("get_source_schema", {"source": "screener"})
    data = json.loads(res.content[0].text)
    assert isinstance(data, dict)
    assert "title" in data


def test_get_source_schema_unknown(mock_db):
    res = _call_tool("get_source_schema", {"source": "nope"}, raise_on_error=False)
    assert res.is_error
    text = "".join(c.text for c in res.content if getattr(c, "text", None))
    assert "InvalidRequestError" in text

import io
from unittest.mock import AsyncMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

import cli
from src.services._common import NotFoundError

runner = CliRunner()


@pytest.fixture
def buf_console():
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system=None)
    with (
        patch("src.cli.render.console", console),
        patch("src.cli.render.error_console", console),
        patch("cli.console", console),
        patch("cli.init_db", AsyncMock()),
    ):
        yield buf


def test_ping(buf_console):
    result = runner.invoke(cli.app, ["ping"])
    assert result.exit_code == 0
    assert "ok" in buf_console.getvalue()


def test_version(buf_console):
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert cli.__version__ in buf_console.getvalue()


def test_list_sources(buf_console):
    sample = {
        "category": "sources",
        "country": "in",
        "source": "NSE",
        "data": [{"name": "NSE", "type": "exchange"}],
    }
    with patch("cli.list_category", return_value=sample):
        result = runner.invoke(cli.app, ["list", "--category", "sources"])
    assert result.exit_code == 0
    assert "sources" in buf_console.getvalue()
    assert "NSE" in buf_console.getvalue()


def test_metrics(buf_console):
    sample = {"symbol": "VBL", "current_price": 442.3, "revenue_growth": 14.64}
    with patch("cli.financial_metrics", AsyncMock(return_value=sample)):
        result = runner.invoke(cli.app, ["metrics", "VBL", "--filing-type", "ttm"])
    assert result.exit_code == 0
    out = buf_console.getvalue()
    assert "VBL" in out
    assert "14.64" in out


def test_pull(buf_console):
    sample = {
        "symbol": "VBL",
        "source": "NSE",
        "status": "completed",
        "records_pulled": 10,
        "xbrl_parsed": 2,
        "endpoint_breakdown": {},
        "timing": {},
    }
    with patch("cli.pull_nse_data", AsyncMock(return_value=sample)):
        result = runner.invoke(cli.app, ["pull", "VBL"])
    assert result.exit_code == 0
    assert "completed" in buf_console.getvalue()


def test_service_error_exits_1(buf_console):
    with patch(
        "cli.get_pull_status",
        AsyncMock(side_effect=NotFoundError("No data found for VBL")),
    ):
        result = runner.invoke(cli.app, ["pull-status", "VBL"])
    assert result.exit_code == 1
    assert "No data found for VBL" in buf_console.getvalue()


def test_funds_not_implemented(buf_console):
    result = runner.invoke(cli.app, ["funds"])
    assert result.exit_code == 0
    assert "not yet implemented" in buf_console.getvalue()

"""Tests for Phase 1 quality fixes: /list arg bug and /financials period
mismatch."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _doc(model, period_end_date, **fields):
    d = {
        "symbol": "T",
        "period_end_date": period_end_date,
        "consolidated": True,
        "source": "NSE",
        "pulled_at": None,
        "_content_hash": None,
        "id": None,
    }
    d.update(fields)
    return SimpleNamespace(to_dict=lambda: d)


# --- Fix 1.1: /list endpoint accepts source as keyword ---


def test_list_sources_endpoint(client):
    resp = client.get("/list", params={"category": "sources", "source": "nse"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "NSE"
    assert body["country"] == "in"
    assert isinstance(body["data"], list)


def test_list_countries_endpoint(client):
    resp = client.get("/list", params={"category": "countries", "source": "nse"})
    assert resp.status_code == 200
    assert resp.json()["category"] == "countries"


def test_list_unsupported_category(client):
    resp = client.get("/list", params={"category": "bogus", "source": "nse"})
    assert resp.status_code == 400


def test_list_wrong_source_for_country(client):
    resp = client.get("/list", params={"category": "sources", "source": "bogus"})
    assert resp.status_code == 501


# --- Fix 1.2: /financials flags mixed reporting periods ---


def _mock_factory(session):
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _session_with_docs(income, balance, cash):
    """get_financials queries income & cash flow via scalar_one_or_none() and
    the balance sheet via scalars().all() (look-back for stub skip)."""
    bs_result = MagicMock()
    bs_scalars = MagicMock()
    bs_scalars.all.return_value = [balance] if balance is not None else []
    bs_result.scalars.return_value = bs_scalars

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(side_effect=[income, cash])

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[result, bs_result, result])
    return session


def test_get_financials_consistent_periods_no_warning():
    from src.services.nse import get_financials

    income = _doc(SimpleNamespace, "2026-06-30", revenue_from_operations=311850)
    balance = _doc(SimpleNamespace, "2026-06-30", assets=1000)
    cash = _doc(
        SimpleNamespace, "2026-06-30",
        cash_flows_from_used_in_operating_activities=42,
    )
    session = _session_with_docs(income, balance, cash)

    def run():
        with patch(
            "src.services.nse.get_session_factory",
            return_value=_mock_factory(session),
        ):
            return asyncio.run(get_financials("T", source="nse"))

    resp = run()
    assert "data_quality" not in resp


def test_get_financials_mixed_periods_flags_warning():
    from src.services.nse import get_financials

    income = _doc(SimpleNamespace, "2026-06-30", revenue_from_operations=311850)
    balance = _doc(SimpleNamespace, "2026-03-31", assets=1000)
    cash = _doc(
        SimpleNamespace, "2021-03-31",
        cash_flows_from_used_in_operating_activities=42,
    )
    session = _session_with_docs(income, balance, cash)

    def run():
        with patch(
            "src.services.nse.get_session_factory",
            return_value=_mock_factory(session),
        ):
            return asyncio.run(get_financials("T", source="nse"))

    resp = run()
    assert resp["data_quality"]["warning"] == (
        "Merged data mixes different reporting periods"
    )
    assert resp["data_quality"]["source_periods"]["CashFlow"] == "2021-03-31"
    assert resp["data_quality"]["source_periods"]["IncomeStatement"] == "2026-06-30"
    # period_end_date must anchor on the income statement, not the stale cash flow
    assert resp["period_end_date"] == "2026-06-30"

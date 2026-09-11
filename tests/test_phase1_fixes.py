"""Tests for Phase 1 quality fixes: /list arg bug, /financials period
mismatch, and /dcf growth cap."""

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


def _session_with_docs(*docs_by_iteration):
    """get_financials iterates IncomeStatement, BalanceSheet, CashFlow in a
    fixed order and calls execute() then scalar_one_or_none() each pass."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(side_effect=list(docs_by_iteration))
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
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


# --- Fix 1.3: /dcf caps auto growth, never user-provided ---


async def _run_dcf(metrics, **kwargs):
    with patch("src.services.dcf.financial_metrics", new=AsyncMock(return_value=metrics)):
        from src.services.dcf import dcf_valuation

        return await dcf_valuation("T", source="nse", **kwargs)


def test_dcf_caps_auto_growth():
    metrics = {
        "free_cash_flow_per_share": 10.0,
        "current_price": 100.0,
        "revenue_growth": 20.0,
    }
    result = asyncio.run(_run_dcf(metrics))
    assert result["assumptions"]["growth_rate"] == 0.12
    assert len(result["warnings"]) == 1
    assert "capped" in result["warnings"][0]


def test_dcf_does_not_cap_user_growth():
    metrics = {
        "free_cash_flow_per_share": 10.0,
        "current_price": 100.0,
        "revenue_growth": 20.0,
    }
    result = asyncio.run(_run_dcf(metrics, growth_rate=0.25))
    assert result["assumptions"]["growth_rate"] == 0.25
    assert result["warnings"] == []


def test_dcf_auto_growth_within_cap_has_no_warning():
    metrics = {
        "free_cash_flow_per_share": 10.0,
        "current_price": 100.0,
        "revenue_growth": 5.0,
    }
    result = asyncio.run(_run_dcf(metrics))
    assert result["assumptions"]["growth_rate"] == 0.05
    assert result["warnings"] == []

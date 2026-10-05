"""Regression: raw NUMERIC columns arrive as Decimal, so any arithmetic that
mixes them with a float raises TypeError and the endpoint 500s. This happened
for real on filing_type=quarterly/annual, where the EBITDA-growth sum skipped
the _to_float wrapper its sibling computation used.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.services.metrics import financial_metrics


def _doc(period, pbt, finance_costs, dep):
    return {
        "period_end_date": period,
        "fiscal_period": "Q1",
        "profit_before_tax": Decimal(pbt),
        "finance_costs": Decimal(finance_costs),
        "depreciation_depletion_and_amortisation_expense": Decimal(dep),
        "profit_loss_for_period": Decimal(pbt),
        "equity_share_capital": Decimal("1000"),
        "other_equity": Decimal("2000"),
    }


class _FakeSession:
    """Two quarterly periods one year apart: current + year-ago."""

    def __init__(self, docs):
        self._docs = docs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        rows = [MagicMock(to_dict=lambda d=d: dict(d)) for d in self._docs]
        return MagicMock(scalars=lambda: MagicMock(all=lambda: rows))


def _factory(docs):
    # production does `async with get_session_factory()()`, so the patch must
    # return a session-maker, not a session.
    maker = lambda: _FakeSession(docs)  # noqa: E731
    return lambda: maker


@pytest.mark.asyncio
@pytest.mark.parametrize("filing_type", ["quarterly", "annual", "ttm"])
async def test_decimal_columns_do_not_crash(filing_type):
    docs = [
        _doc(date(2026, 6, 30), "5000", "500", "300"),
        _doc(date(2025, 6, 30), "4000", "400", "250"),
    ]
    with patch(
        "src.services.metrics.get_session_factory", _factory(docs)
    ), patch(
        "src.tools.nse.technicals.fetch_price_info",
        lambda symbol, exchange: {
            "current_price": 100.0,
            "shares_outstanding": 10,
        },
    ):
        out = await financial_metrics("TCS", filing_type=filing_type)

    assert out["symbol"] == "TCS"
    assert out["price_data"] == "live"
    # ebitda growth is only computed per-filing basis, not on the TTM window.
    if filing_type == "ttm":
        return
    assert isinstance(out["ebitda_growth_annual"], float)

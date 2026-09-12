"""Unit tests for the SEC service's pure data-shaping helpers.

These avoid EDGAR/network and the DB entirely.
"""

import pandas as pd
import pytest

from src.services.sec import (
    _DEFAULT_IDENTITY,
    _diff_cumulative,
    _fiscal_period,
    _period_to_date,
    _q4_rows,
)

_INCOME = ["us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax"]
_OCF = ["us-gaap_NetCashProvidedByUsedInOperatingActivities"]


def _income_frame(values):
    return pd.DataFrame({"concept": _INCOME, **{k: [v] for k, v in values.items()}})


def test_diff_cumulative_within_fiscal_year():
    df = _income_frame({"2025-12-27": 143.7, "2026-03-28": 254.9, "2026-06-27": 364.4})
    out = _diff_cumulative(df, list(df.columns[1:]))
    assert out["2025-12-27"].iloc[0] == pytest.approx(143.7)
    assert out["2026-03-28"].iloc[0] == pytest.approx(111.2)
    assert out["2026-06-27"].iloc[0] == pytest.approx(109.5)


def test_diff_cumulative_across_fiscal_years():
    df = _income_frame({"2025-06-28": 100.0, "2025-12-27": 120.0, "2026-03-28": 140.0})
    out = _diff_cumulative(df, list(df.columns[1:]))
    assert out["2025-06-28"].iloc[0] == pytest.approx(100.0)
    assert out["2025-12-27"].iloc[0] == pytest.approx(120.0)
    assert out["2026-03-28"].iloc[0] == pytest.approx(20.0)


def test_q4_rows_income_and_cashflow():
    annual = pd.DataFrame(
        {
            "concept": [
                "us-gaap_NetIncomeLoss",
                "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic",
            ],
            "2025-09-27": [12.0, 4.0],
        }
    )
    ytd = pd.DataFrame(
        {
            "concept": [
                "us-gaap_NetIncomeLoss",
                "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic",
            ],
            "2025-06-28": [9.0, 3.0],
        }
    )
    rows = _q4_rows("X", "income", annual, ytd, "2025-09-27", "2025-06-28")
    assert len(rows) == 1
    assert rows[0]["period_end_date"].isoformat() == "2025-09-27"
    assert rows[0]["profit_loss_for_period"] == pytest.approx(3.0)
    assert rows[0]["basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"] == pytest.approx(
        0.75
    )

    cf_annual = pd.DataFrame({"concept": _OCF, "2025-09-27": [50.0]})
    cf_ytd = pd.DataFrame({"concept": _OCF, "2025-06-28": [30.0]})
    cf_rows = _q4_rows("X", "cashflow", cf_annual, cf_ytd, "2025-09-27", "2025-06-28")
    assert cf_rows[0]["cash_flows_from_used_in_operations"] == pytest.approx(20.0)
    assert cf_rows[0]["source_endpoint"] == "10-Q"


def test_default_identity_is_declared_and_not_previously_blocked():
    # SEC's bot filter learned the old "VoyagerData/1.0 (github...)" app token
    # and 403s it everywhere; the default must stay a fresh declared identity.
    assert _DEFAULT_IDENTITY.startswith("Voyager/1.0 (")
    assert "VoyagerData" not in _DEFAULT_IDENTITY
    assert "github.com" not in _DEFAULT_IDENTITY


def test_q4_rows_skips_mismatched_concepts():
    annual = pd.DataFrame({"concept": ["us-gaap_NetIncomeLoss"], "2025-09-27": [12.0]})
    ytd = pd.DataFrame({"concept": ["us-gaap_SalesRevenueNet"], "2025-06-28": [9.0]})
    assert _q4_rows("X", "income", annual, ytd, "2025-09-27", "2025-06-28") == []


def test_fiscal_period_and_period_to_date():
    assert _fiscal_period(pd.Timestamp("2026-06-27").date()) == "Q1"
    assert _fiscal_period(pd.Timestamp("2025-09-27").date()) == "Q2"
    assert _fiscal_period(pd.Timestamp("2026-01-27").date()) == "Q4"
    assert _period_to_date("2025-09-27").isoformat() == "2025-09-27"
    assert _period_to_date("2025-09-27 (FY)") is None
    assert _period_to_date("nope") is None

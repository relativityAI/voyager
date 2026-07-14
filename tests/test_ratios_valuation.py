
import pytest

from src.tools.nse.valuation import (
    compute_shares_outstanding,
    compute_valuation,
    get_valuation_catalog,
)


class TestComputeSharesOutstanding:
    def test_normal(self):
        data = {
            "PaidUpValueOfEquityShareCapital": "1000000000",
            "FaceValueOfEquityShareCapital": "10",
        }
        assert compute_shares_outstanding(data) == 100000000.0

    def test_missing_face_value(self):
        data = {"PaidUpValueOfEquityShareCapital": "1000000000"}
        assert compute_shares_outstanding(data) is None

    def test_zero_face_value(self):
        data = {
            "PaidUpValueOfEquityShareCapital": "1000000000",
            "FaceValueOfEquityShareCapital": "0",
        }
        assert compute_shares_outstanding(data) is None

    def test_nan_values(self):
        data = {
            "PaidUpValueOfEquityShareCapital": float("nan"),
            "FaceValueOfEquityShareCapital": "10",
        }
        assert compute_shares_outstanding(data) is None

    def test_empty_data(self):
        assert compute_shares_outstanding({}) is None


class TestComputeValuation:
    COMPLETE_DATA = {
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "50.0",
        "PaidUpValueOfEquityShareCapital": "1000000000",
        "ReserveExcludingRevaluationReserves": "5000000000",
        "RevenueFromOperations": "20000000000",
        "CashFlowsFromUsedInOperatingActivities": "3000000000",
    }

    def test_all_ratios_with_valid_data(self):
        result = compute_valuation(self.COMPLETE_DATA, 2500.0, 100000000, eps_growth=15.0)
        assert result["pe_ratio"] == 50.0  # 2500/50
        assert result["pb_ratio"] == pytest.approx(41.6667, rel=1e-3)  # 2500 / (6000M/100M)
        assert result["ps_ratio"] == 12.5  # 2500 / (20000M/100M)
        assert result["pcf_ratio"] == pytest.approx(83.3333, rel=1e-3)  # 2500 / (3000M/100M)
        assert result["peg_ratio"] == pytest.approx(3.3333, rel=1e-3)  # 50 / 15

    def test_all_none_when_no_price(self):
        result = compute_valuation(self.COMPLETE_DATA, None, 100000000)
        assert all(v is None for v in result.values())

    def test_all_none_when_nan_price(self):
        result = compute_valuation(self.COMPLETE_DATA, float("nan"), 100000000)
        assert all(v is None for v in result.values())

    def test_all_none_when_zero_price(self):
        result = compute_valuation(self.COMPLETE_DATA, 0, 100000000)
        assert all(v is None for v in result.values())

    def test_pe_with_negative_eps(self):
        data = {"BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "-10"}
        result = compute_valuation(data, 100.0, 100000000)
        assert result["pe_ratio"] == -10.0  # negative PE is valid

    def test_pe_only_with_no_shares(self):
        result = compute_valuation(
            {"BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "25"},
            500.0,
            0,
        )
        assert result["pe_ratio"] == 20.0  # 500/25
        assert result["pb_ratio"] is None  # needs shares
        assert result["ps_ratio"] is None
        assert result["pcf_ratio"] is None

    def test_fallback_shares_from_financials(self):
        data = {
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "10",
            "PaidUpValueOfEquityShareCapital": "500000000",
            "FaceValueOfEquityShareCapital": "10",
            "EquityShareCapital": "500000000",
            "OtherEquity": "2000000000",
        }
        result = compute_valuation(data, 100.0, None)
        assert result["pe_ratio"] == 10.0
        # equity=2500M, shares=50M, bvps=50, pb=100/50=2.0
        assert result["pb_ratio"] == pytest.approx(2.0, rel=1e-3)

    def test_no_peg_with_zero_growth(self):
        result = compute_valuation(self.COMPLETE_DATA, 2500.0, 100000000, eps_growth=0)
        assert result["pe_ratio"] is not None
        assert result["peg_ratio"] is None

    def test_no_peg_with_negative_growth(self):
        result = compute_valuation(self.COMPLETE_DATA, 2500.0, 100000000, eps_growth=-10.0)
        assert result["pe_ratio"] is not None
        assert result["peg_ratio"] is None

    def test_zero_revenue(self):
        data = {
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "10",
            "EquityShareCapital": "100000000",
            "OtherEquity": "400000000",
            "RevenueFromOperations": "0",
        }
        result = compute_valuation(data, 100.0, 50000000)
        assert result["pe_ratio"] == 10.0
        assert result["ps_ratio"] is None  # zero revenue

    def test_zero_equity(self):
        data = {
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "10",
            "RevenueFromOperations": "10000000000",
            "CashFlowsFromUsedInOperatingActivities": "2000000000",
        }
        result = compute_valuation(data, 500.0, 50000000)
        assert result["pe_ratio"] == 50.0
        assert result["pb_ratio"] is None  # zero equity
        assert result["ps_ratio"] == 2.5  # 500 / (10000M/50M)
        assert result["pcf_ratio"] == 12.5  # 500 / (2000M/50M)


class TestGetValuationCatalog:
    def test_returns_dict(self):
        catalog = get_valuation_catalog()
        assert isinstance(catalog, dict)
        assert catalog["id"] == "ratio_valuation"

    def test_contains_five_metrics(self):
        catalog = get_valuation_catalog()
        assert len(catalog["metrics"]) == 5
        ids = [m["id"] for m in catalog["metrics"]]
        assert "pe_ratio" in ids
        assert "pb_ratio" in ids
        assert "ps_ratio" in ids
        assert "pcf_ratio" in ids
        assert "peg_ratio" in ids


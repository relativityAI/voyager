
from src.tools.nse.ratios import (
    ALL_CATEGORIES,
    compute_growth,
    compute_static,
    flatten_financials,
    get_metrics_catalog,
    pct,
    safe_div,
    to_float,
)


class TestToFloat:
    def test_none(self):
        assert to_float(None) is None

    def test_empty_string(self):
        assert to_float("") is None

    def test_commas(self):
        assert to_float("1,234.56") == 1234.56

    def test_nan(self):
        assert to_float(float("nan")) is None

    def test_inf(self):
        assert to_float(float("inf")) is None

    def test_valid_float(self):
        assert to_float("123.45") == 123.45

    def test_valid_number(self):
        assert to_float(42) == 42.0


class TestSafeDiv:
    def test_none_numerator(self):
        assert safe_div(None, 5.0) is None

    def test_none_denominator(self):
        assert safe_div(5.0, None) is None

    def test_zero_denominator(self):
        assert safe_div(5.0, 0) is None

    def test_normal(self):
        assert safe_div(10.0, 2.0) == 5.0

    def test_nan_result(self):
        assert safe_div(0.0, 0.0) is None

    def test_inf_result(self):
        assert safe_div(float("inf"), 1.0) is None


class TestPct:
    def test_none(self):
        assert pct(None) is None

    def test_normal(self):
        assert pct(0.15) == 15.0

    def test_rounding(self):
        assert pct(0.123456) == 12.3456


class TestFlattenFinancials:
    def test_empty(self):
        assert flatten_financials([]) == {}

    def test_normal(self):
        data = [{"tag": "Revenue", "value": "1000"}, {"tag": "Profit", "value": "200"}]
        result = flatten_financials(data)
        assert result == {"Revenue": "1000", "Profit": "200"}

    def test_duplicate_tags(self):
        # Prefers quarterly (OneI) over non-quarterly, and first match for same type
        data = [
            {"tag": "Revenue", "value": "1000"},
            {"tag": "Revenue", "value": "2000", "contextRef": "OneI"},
        ]
        result = flatten_financials(data)
        assert result == {"Revenue": "2000"}

    def test_duplicate_tags_first_wins(self):
        # First non-quarterly wins; quarterly overrides after
        data = [
            {"tag": "Revenue", "value": "1000"},
            {"tag": "Revenue", "value": "2000"},
        ]
        result = flatten_financials(data)
        assert result == {"Revenue": "1000"}


class TestComputeStatic:
    FULL_DATA = {
        "RevenueFromOperations": "100000",
        "ProfitLossForPeriod": "15000",
        "ProfitBeforeTax": "20000",
        "FinanceCosts": "3000",
        "Expenses": "80000",
        "Assets": "500000",
        "EquityShareCapital": "50000",
        "OtherEquity": "150000",
        "NoncurrentLiabilities": "100000",
        "DebtEquityRatio": "1.5",
        "BorrowingsCurrent": "20000",
        "CashAndCashEquivalents": "10000",
        "CashFlowsFromUsedInOperatingActivities": "25000",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "10",
        "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "9.5",
    }

    def test_all_ratios_computed(self):
        result = compute_static(self.FULL_DATA)
        ratio_count = sum(len(r) for _, r in ALL_CATEGORIES)
        computed_count = sum(len(v) for v in result.values())
        assert computed_count == ratio_count

    def test_net_profit_margin(self):
        result = compute_static(self.FULL_DATA)
        margin = result["profitability"]["net_profit_margin"]
        assert margin == 15.0  # 15000/100000 * 100

    def test_operating_margin(self):
        result = compute_static(self.FULL_DATA)
        margin = result["profitability"]["operating_margin"]
        expected = ((20000 + 3000) / 100000) * 100
        assert margin == round(expected, 4)

    def test_pre_tax_margin(self):
        result = compute_static(self.FULL_DATA)
        assert result["profitability"]["pre_tax_margin"] == 20.0

    def test_roe(self):
        result = compute_static(self.FULL_DATA)
        equity = 50000 + 150000
        expected = (15000 / equity) * 100
        assert result["return"]["roe"] == round(expected, 4)

    def test_roa(self):
        result = compute_static(self.FULL_DATA)
        expected = (15000 / 500000) * 100
        assert result["return"]["roa"] == expected

    def test_roce(self):
        result = compute_static(self.FULL_DATA)
        capital_employed = 500000 - 100000
        ebit = 20000 + 3000  # PBT + FinanceCosts
        expected = (ebit / capital_employed) * 100
        assert result["return"]["roce"] == round(expected, 4)

    def test_debt_to_equity(self):
        result = compute_static(self.FULL_DATA)
        assert result["capital_structure"]["debt_to_equity"] == 1.5

    def test_equity_ratio(self):
        result = compute_static(self.FULL_DATA)
        expected = ((50000 + 150000) / 500000) * 100
        assert result["capital_structure"]["equity_ratio"] == expected

    def test_financial_leverage(self):
        result = compute_static(self.FULL_DATA)
        expected = 500000 / (50000 + 150000)
        assert result["capital_structure"]["financial_leverage"] == expected

    def test_cash_ratio(self):
        result = compute_static(self.FULL_DATA)
        expected = 10000 / 20000
        assert result["liquidity"]["cash_ratio"] == expected

    def test_ocf_margin(self):
        result = compute_static(self.FULL_DATA)
        expected = (25000 / 100000) * 100
        assert result["cash_flow"]["operating_cash_flow_margin"] == expected

    def test_ocf_to_net_income(self):
        result = compute_static(self.FULL_DATA)
        expected = 25000 / 15000
        assert result["cash_flow"]["ocf_to_net_income"] == expected

    def test_dilution_impact(self):
        result = compute_static(self.FULL_DATA)
        expected = ((10 - 9.5) / 10) * 100
        assert result["earnings_quality"]["dilution_impact"] == expected

    def test_missing_field_returns_none(self):
        result = compute_static({})
        for cat_key, cat_data in result.items():
            for ratio_val in cat_data.values():
                assert ratio_val is None, f"{cat_key} expected None got {ratio_val}"

    def test_partial_data(self):
        data = {"RevenueFromOperations": "1000", "ProfitLossForPeriod": "100"}
        result = compute_static(data)
        assert result["profitability"]["net_profit_margin"] == 10.0
        assert result["profitability"]["operating_margin"] is None


class TestComputeGrowth:
    CURRENT = {
        "RevenueFromOperations": "120000",
        "ProfitLossForPeriod": "18000",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "12",
        "EquityShareCapital": "60000",
    }
    PREVIOUS = {
        "RevenueFromOperations": "100000",
        "ProfitLossForPeriod": "15000",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "10",
        "EquityShareCapital": "50000",
    }

    def test_revenue_growth(self):
        growth = compute_growth(self.CURRENT, self.PREVIOUS)
        expected = ((120000 - 100000) / 100000) * 100
        assert growth["revenue_growth"] == expected

    def test_eps_growth(self):
        growth = compute_growth(self.CURRENT, self.PREVIOUS)
        expected = ((12 - 10) / 10) * 100
        assert growth["eps_growth"] == expected

    def test_no_previous_returns_none(self):
        growth = compute_growth(self.CURRENT, {})
        assert all(v is None for v in growth.values())

    def test_zero_previous(self):
        prev = {"RevenueFromOperations": "0"}
        growth = compute_growth(self.CURRENT, prev)
        assert growth["revenue_growth"] is None


class TestGetMetricsCatalog:
    def test_returns_list(self):
        catalog = get_metrics_catalog()
        assert isinstance(catalog, list)

    def test_contains_raw_categories(self):
        catalog = get_metrics_catalog()
        ids = [c["id"] for c in catalog]
        assert "raw_income_statement" in ids
        assert "raw_balance_sheet" in ids

    def test_contains_ratio_categories(self):
        catalog = get_metrics_catalog()
        ids = [c["id"] for c in catalog]
        assert "ratio_profitability" in ids
        assert "ratio_return" in ids
        assert "ratio_capital_structure" in ids
        assert "ratio_liquidity" in ids
        assert "ratio_cash_flow" in ids
        assert "ratio_earnings_quality" in ids

    def test_contains_growth(self):
        catalog = get_metrics_catalog()
        ids = [c["id"] for c in catalog]
        assert "ratio_growth" in ids

    def test_metrics_have_id_and_name(self):
        catalog = get_metrics_catalog()
        for category in catalog:
            for metric in category["metrics"]:
                assert "id" in metric
                assert "name" in metric

    def test_trimmed_ratio_count(self):
        catalog = get_metrics_catalog()
        ratio_cats = [c for c in catalog if c["type"] == "ratio"]
        total_ratio_metrics = sum(len(c["metrics"]) for c in ratio_cats)
        assert total_ratio_metrics <= 25  # trimmed to 1/2 of original 54

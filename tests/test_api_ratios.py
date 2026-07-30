from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from fastapi.testclient import TestClient

    from api import app

    client = TestClient(app)
    HAS_API = True
except ImportError:
    HAS_API = False
    pytest.skip("Skipping API tests: motor/pymongo import issue", allow_module_level=True)


class TestFinancialMetrics:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        from unittest.mock import patch

        price_patcher = patch("src.tools.nse.technicals.fetch_price_info")
        tech_patcher = patch("src.tools.nse.technicals.fetch_technicals")
        db_patcher = patch("api.get_database")

        self.mock_fetch_price = price_patcher.start()
        self.mock_fetch_tech = tech_patcher.start()
        self.mock_get_db = db_patcher.start()

        yield

        price_patcher.stop()
        tech_patcher.stop()
        db_patcher.stop()

    def _make_records(self, records_list):
        """Convert flat dict list into per-collection iterables."""
        income = []
        balance = []
        cashflow = []
        for rec in records_list:
            income.append({
                "period_end_date": rec["period_end_date"],
                "consolidated": rec["consolidated"],
                "revenue_from_operations": rec.get("revenue_from_operations"),
                "profit_loss_for_period": rec.get("profit_loss_for_period"),
                "profit_before_tax": rec.get("profit_before_tax"),
                "finance_costs": rec.get("finance_costs"),
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": rec.get("basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"),
                "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations": rec.get("diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations"),
                "paid_up_value_of_equity_share_capital": rec.get("paid_up_value_of_equity_share_capital"),
                "face_value_of_equity_share_capital": rec.get("face_value_of_equity_share_capital"),
            })
            balance.append({
                "period_end_date": rec["period_end_date"],
                "consolidated": rec["consolidated"],
                "equity_share_capital": rec.get("equity_share_capital"),
                "other_equity": rec.get("other_equity"),
                "assets": rec.get("assets"),
                "noncurrent_liabilities": rec.get("noncurrent_liabilities"),
                "debt_equity_ratio": rec.get("debt_equity_ratio"),
                "borrowings_current": rec.get("borrowings_current"),
                "cash_and_cash_equivalents": rec.get("cash_and_cash_equivalents"),
            })
            cashflow.append({
                "period_end_date": rec["period_end_date"],
                "consolidated": rec["consolidated"],
                "cash_flows_from_used_in_operating_activities": rec.get("cash_flows_from_used_in_operating_activities"),
            })
        return income, balance, cashflow

    def _setup_db_mock(self, records_list):
        mock_db = MagicMock()
        income, balance, cashflow = self._make_records(records_list)

        def db_getitem(name):
            colls = {
                "income_statements": self._make_cursor(income),
                "balance_sheets": self._make_cursor(balance),
                "cash_flows": self._make_cursor(cashflow),
            }
            mock = MagicMock()
            mock.find.return_value = colls.get(name, self._make_empty_cursor())
            return mock

        mock_db.__getitem__.side_effect = db_getitem
        return mock_db

    def _make_cursor(self, items):
        cur = MagicMock()
        cur.sort.return_value = cur
        cur.__aiter__.return_value = iter(items)
        cur.to_list = AsyncMock(return_value=items)
        return cur

    def _make_empty_cursor(self):
        cur = MagicMock()
        cur.sort.return_value = cur
        cur.__aiter__.return_value = iter([])
        return cur

    def test_successful_response(self):
        self.mock_get_db.return_value = self._setup_db_mock([
            {
                "period_end_date": "2024-12-31",
                "consolidated": True,
                "revenue_from_operations": "100000",
                "profit_loss_for_period": "15000",
                "profit_before_tax": "20000",
                "finance_costs": "3000",
                "noncurrent_liabilities": "100000",
                "debt_equity_ratio": "1.5",
                "borrowings_current": "20000",
                "cash_and_cash_equivalents": "10000",
                "cash_flows_from_used_in_operating_activities": "25000",
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": "10",
                "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations": "9.5",
                "paid_up_value_of_equity_share_capital": "500000",
                "face_value_of_equity_share_capital": "10",
                "equity_share_capital": "50000",
                "other_equity": "150000",
                "assets": "500000",
            },
            {
                "period_end_date": "2024-09-30",
                "consolidated": True,
                "revenue_from_operations": "90000",
                "profit_loss_for_period": "12000",
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": "8",
                "equity_share_capital": "50000",
                "other_equity": "140000",
                "assets": "450000",
            },
        ])

        self.mock_fetch_price.return_value = {
            "current_price": 2500.0,
            "shares_outstanding": 50000000,
        }
        self.mock_fetch_tech.return_value = {
            "current_price": 2500.0,
            "rsi_14": 55.5,
            "sma_20": 2450.0,
        }

        response = client.get(
            "/financial-metrics?symbol=TEST&country=in&source=nse&compute_ratios=true"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "TEST"
        assert len(data["ratios"]) >= 1
        assert "valuation" in data
        assert "pe_ratio" in data["valuation"]
        assert "pb_ratio" in data["valuation"]
        assert "ps_ratio" in data["valuation"]
        assert "growth" in data["ratios"][0]
        assert "eps_growth_qoq" in data["ratios"][0]["growth"]
        assert "revenue_growth_qoq" in data["ratios"][0]["growth"]
        assert "technicals" in data
        assert data["technicals"]["rsi_14"] == 55.5

    def test_404_for_unknown_symbol(self):
        self.mock_get_db.return_value = self._setup_db_mock([])

        response = client.get(
            "/financial-metrics?symbol=UNKNOWN&country=in&source=nse&compute_ratios=true"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ratios"] is None

    def test_no_nan_in_json_response(self):
        self.mock_get_db.return_value = self._setup_db_mock([
            {
                "period_end_date": "2024-12-31",
                "consolidated": True,
                "revenue_from_operations": "100000",
                "profit_loss_for_period": "15000",
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": "10",
                "equity_share_capital": "50000",
                "other_equity": "150000",
                "assets": "500000",
            },
        ])

        self.mock_fetch_price.return_value = {
            "current_price": 2500.0,
            "shares_outstanding": 50000000,
        }
        self.mock_fetch_tech.return_value = {
            "current_price": 2500.0,
            "rsi_14": 55.5,
        }

        response = client.get(
            "/financial-metrics?symbol=TEST&country=in&source=nse&compute_ratios=true"
        )
        assert response.status_code == 200
        body = response.text
        assert "NaN" not in body
        assert "Infinity" not in body

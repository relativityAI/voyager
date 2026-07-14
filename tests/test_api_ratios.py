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


class TestAvailableMetrics:
    def test_returns_200(self):
        response = client.get("/equity/data/metrics/available")
        assert response.status_code == 200

    def test_contains_raw_categories(self):
        response = client.get("/equity/data/metrics/available")
        data = response.json()
        categories = {c["id"]: c for c in data["categories"]}
        assert "raw_income_statement" in categories
        assert "raw_balance_sheet" in categories

    def test_contains_valuation_ratios(self):
        response = client.get("/equity/data/metrics/available")
        data = response.json()
        categories = {c["id"]: c for c in data["categories"]}
        assert "ratio_valuation" in categories

    def test_contains_technicals(self):
        response = client.get("/equity/data/metrics/available")
        data = response.json()
        categories = {c["id"]: c for c in data["categories"]}
        assert "technicals" in categories

    def test_total_categories_count(self):
        response = client.get("/equity/data/metrics/available")
        data = response.json()
        assert len(data["categories"]) == 14

    def test_metrics_have_id_and_name(self):
        response = client.get("/equity/data/metrics/available")
        data = response.json()
        for category in data["categories"]:
            for metric in category["metrics"]:
                assert "id" in metric
                assert "name" in metric


class TestFinancialRatios:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        from unittest.mock import patch

        price_patcher = patch("api.fetch_price_info")
        tech_patcher = patch("api.fetch_technicals")
        db_patcher = patch("api.get_database")

        self.mock_fetch_price = price_patcher.start()
        self.mock_fetch_tech = tech_patcher.start()
        self.mock_get_db = db_patcher.start()

        yield

        price_patcher.stop()
        tech_patcher.stop()
        db_patcher.stop()

    def _setup_db_mock(self, financials_list):
        mock_db = MagicMock()
        mock_coll_q = AsyncMock()
        mock_coll_a = AsyncMock()

        def db_getitem(name):
            colls = {
                "nse_quarterly_financials": mock_coll_q,
                "nse_annual_financials": mock_coll_a,
            }
            return colls.get(name, AsyncMock())

        mock_db.__getitem__.side_effect = db_getitem

        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.__aiter__.return_value = iter(financials_list)
        mock_coll_q.find.return_value = mock_cursor

        mock_cursor_a = MagicMock()
        mock_cursor_a.sort.return_value = mock_cursor_a
        mock_cursor_a.__aiter__.return_value = iter([])
        mock_coll_a.find.return_value = mock_cursor_a

        return mock_db

    def test_successful_response(self):
        self.mock_get_db.return_value = self._setup_db_mock([
            {
                "date": "2024-12-31",
                "consolidated": "Consolidated",
                "source_endpoint": "quarterly-results",
                "broadcast_date": "2025-01-15",
                "financials": [
                    {"tag": "RevenueFromOperations", "value": "100000"},
                    {"tag": "ProfitLossForPeriod", "value": "15000"},
                    {"tag": "ProfitBeforeTax", "value": "20000"},
                    {"tag": "FinanceCosts", "value": "3000"},
                    {"tag": "NoncurrentLiabilities", "value": "100000"},
                    {"tag": "DebtEquityRatio", "value": "1.5"},
                    {"tag": "BorrowingsCurrent", "value": "20000"},
                    {"tag": "CashAndCashEquivalents", "value": "10000"},
                    {"tag": "CashFlowsFromUsedInOperatingActivities", "value": "25000"},
                    {"tag": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "value": "10"},
                    {"tag": "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "value": "9.5"},
                    {"tag": "PaidUpValueOfEquityShareCapital", "value": "500000"},
                    {"tag": "FaceValueOfEquityShareCapital", "value": "10"},
                    {"tag": "EquityShareCapital", "value": "50000"},
                    {"tag": "OtherEquity", "value": "150000"},
                    {"tag": "Assets", "value": "500000"},
                ],
            },
            {
                "date": "2024-09-30",
                "consolidated": "Consolidated",
                "source_endpoint": "quarterly-results",
                "broadcast_date": "2024-10-15",
                "financials": [
                    {"tag": "RevenueFromOperations", "value": "90000"},
                    {"tag": "ProfitLossForPeriod", "value": "12000"},
                    {"tag": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "value": "8"},
                    {"tag": "EquityShareCapital", "value": "50000"},
                    {"tag": "OtherEquity", "value": "140000"},
                    {"tag": "Assets", "value": "450000"},
                ],
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
            "/equity/data/ratios?symbol=TEST&country=in&source=nse&consolidated=Consolidated"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "TEST"
        assert data["current_price"] == 2500.0
        assert len(data["records"]) == 2
        assert "valuation" in data
        assert "pe_ratio" in data["valuation"]
        assert "pb_ratio" in data["valuation"]
        assert "ps_ratio" in data["valuation"]
        assert "records" in data
        assert "ratios" in data["records"][0]
        assert "growth" in data["records"][0]["ratios"]
        assert "eps_growth_qoq" in data["records"][0]["ratios"]["growth"]
        assert "revenue_growth_qoq" in data["records"][0]["ratios"]["growth"]
        assert "technicals" in data
        assert data["technicals"]["rsi_14"] == 55.5

    def test_404_for_unknown_symbol(self):
        self.mock_get_db.return_value = self._setup_db_mock([])

        response = client.get(
            "/equity/data/ratios?symbol=UNKNOWN&country=in&source=nse&consolidated=Consolidated"
        )
        assert response.status_code == 404

    def test_no_nan_in_json_response(self):
        self.mock_get_db.return_value = self._setup_db_mock([
            {
                "date": "2024-12-31",
                "consolidated": "Consolidated",
                "source_endpoint": "quarterly-results",
                "financials": [
                    {"tag": "RevenueFromOperations", "value": "100000"},
                    {"tag": "ProfitLossForPeriod", "value": "15000"},
                    {"tag": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "value": "10"},
                    {"tag": "EquityShareCapital", "value": "50000"},
                    {"tag": "OtherEquity", "value": "150000"},
                    {"tag": "Assets", "value": "500000"},
                ],
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
            "/equity/data/ratios?symbol=TEST&country=in&source=nse&consolidated=Consolidated"
        )
        assert response.status_code == 200
        body = response.text
        assert "NaN" not in body
        assert "Infinity" not in body

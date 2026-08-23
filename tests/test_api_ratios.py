from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from fastapi.testclient import TestClient

    from api import app

    client = TestClient(app)
    HAS_API = True
except ImportError:
    HAS_API = False
    pytest.skip(
        "Skipping API tests: import issue", allow_module_level=True
    )


class TestFinancialMetrics:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        from unittest.mock import patch

        price_patcher = patch("src.tools.nse.technicals.fetch_price_info")
        tech_patcher = patch("src.tools.nse.technicals.fetch_technicals")
        factory_patcher = patch("src.services.metrics.get_session_factory")

        self.mock_fetch_price = price_patcher.start()
        self.mock_fetch_tech = tech_patcher.start()
        self.mock_factory = factory_patcher.start()

        yield

        price_patcher.stop()
        tech_patcher.stop()
        factory_patcher.stop()

    def _make_records(self, records_list):
        """Convert flat dict list into per-collection iterables."""
        income = []
        balance = []
        cashflow = []
        for rec in records_list:
            income.append(
                {
                    "period_end_date": rec["period_end_date"],
                    "consolidated": rec["consolidated"],
                    "revenue_from_operations": rec.get("revenue_from_operations"),
                    "profit_loss_for_period": rec.get("profit_loss_for_period"),
                    "profit_before_tax": rec.get("profit_before_tax"),
                    "finance_costs": rec.get("finance_costs"),
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": rec.get(
                        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                    ),
                    "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations": rec.get(
                        "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                    ),
                    "paid_up_value_of_equity_share_capital": rec.get(
                        "paid_up_value_of_equity_share_capital"
                    ),
                    "face_value_of_equity_share_capital": rec.get(
                        "face_value_of_equity_share_capital"
                    ),
                }
            )
            balance.append(
                {
                    "period_end_date": rec["period_end_date"],
                    "consolidated": rec["consolidated"],
                    "equity_share_capital": rec.get("equity_share_capital"),
                    "other_equity": rec.get("other_equity"),
                    "assets": rec.get("assets"),
                    "noncurrent_liabilities": rec.get("noncurrent_liabilities"),
                    "debt_equity_ratio": rec.get("debt_equity_ratio"),
                    "borrowings_current": rec.get("borrowings_current"),
                    "cash_and_cash_equivalents": rec.get("cash_and_cash_equivalents"),
                }
            )
            cashflow.append(
                {
                    "period_end_date": rec["period_end_date"],
                    "consolidated": rec["consolidated"],
                    "cash_flows_from_used_in_operating_activities": rec.get(
                        "cash_flows_from_used_in_operating_activities"
                    ),
                }
            )
        return income, balance, cashflow

    def _make_mock_model(self, data):
        """Create a mock SQLAlchemy model with a to_dict() method."""
        mock = MagicMock()
        mock.to_dict.return_value = data
        return mock

    def _setup_db_mock(self, records_list):
        income, balance, cashflow = self._make_records(records_list)

        data_map = {
            "income_statements": income,
            "balance_sheets": balance,
            "cash_flows": cashflow,
        }
        query_count = {"n": 0}

        def _make_result(items):
            mock_result = MagicMock()
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [self._make_mock_model(d) for d in items]
            mock_result.scalars.return_value = mock_scalars
            return mock_result

        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__exit__ = AsyncMock(return_value=False)
        self.mock_factory.return_value = MagicMock(return_value=mock_cm)

        def execute_side_effect(stmt):
            # The metrics code queries in order: IncomeStatement, BalanceSheet, CashFlow
            idx = query_count["n"]
            query_count["n"] += 1
            keys = list(data_map.keys())
            if idx < len(keys):
                return _make_result(data_map[keys[idx]])
            return _make_result([])

        mock_session.execute = AsyncMock(side_effect=execute_side_effect)
        return mock_session

    def test_successful_response(self):
        self._setup_db_mock(
            [
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
            ]
        )

        self.mock_fetch_price.return_value = {
            "current_price": 2500.0,
            "shares_outstanding": 50000000,
        }
        self.mock_fetch_tech.return_value = {
            "current_price": 2500.0,
            "rsi_14": 55.5,
            "sma_20": 2450.0,
        }

        response = client.get("/financial-metrics?symbol=TEST&country=in&source=nse")
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "TEST"
        assert data["filing_type"] == "quarterly"
        assert data["period_end_date"] == "2024-12-31"
        assert data["price_to_earnings_ratio"] == pytest.approx(250.0)
        assert data["net_margin"] == pytest.approx(15.0)
        assert data["rsi_14"] == 55.5

    def test_price_fetch_error_still_returns_metrics(self):
        """A yfinance rate-limit must degrade to nulls, not raise a 500."""
        from yfinance.exceptions import YFRateLimitError

        self._setup_db_mock(
            [
                {
                    "period_end_date": "2024-12-31",
                    "consolidated": True,
                    "revenue_from_operations": "100000",
                    "profit_loss_for_period": "15000",
                    "profit_before_tax": "20000",
                    "finance_costs": "3000",
                    "equity_share_capital": "50000",
                    "other_equity": "150000",
                    "assets": "500000",
                }
            ]
        )
        self.mock_fetch_price.side_effect = YFRateLimitError()
        self.mock_fetch_tech.side_effect = YFRateLimitError()

        response = client.get("/financial-metrics?symbol=TEST&country=in&source=nse")
        assert response.status_code == 200
        data = response.json()
        assert data["price_data"] == "unavailable"
        assert data.get("current_price") is None
        assert data["net_margin"] == pytest.approx(15.0)
        assert data["return_on_equity"] == pytest.approx(7.5)

    def test_empty_response_for_unknown_symbol(self):
        self._setup_db_mock([])

        response = client.get("/financial-metrics?symbol=UNKNOWN&country=in&source=nse")
        assert response.status_code == 200
        assert response.json() == {}

    def test_no_nan_in_json_response(self):
        self._setup_db_mock(
            [
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
            ]
        )

        self.mock_fetch_price.return_value = {
            "current_price": 2500.0,
            "shares_outstanding": 50000000,
        }
        self.mock_fetch_tech.return_value = {
            "current_price": 2500.0,
            "rsi_14": 55.5,
        }

        response = client.get("/financial-metrics?symbol=TEST&country=in&source=nse")
        assert response.status_code == 200
        body = response.text
        assert "NaN" not in body
        assert "Infinity" not in body

    def _make_ttm_quarterly_records(self):
        """8 quarterly records (desc): [0:4] current TTM window, [4:8] prior TTM window."""
        rows = [
            ("2025-03-31", 100, 15, 20, 3, 10, 25),
            ("2024-12-31", 90, 13, 18, 3, 9, 20),
            ("2024-09-30", 80, 11, 16, 2, 8, 18),
            ("2024-06-30", 70, 9, 14, 2, 7, 15),
            ("2024-03-31", 60, 7, 12, 2, 6, 12),
            ("2023-12-31", 50, 6, 10, 2, 5, 10),
            ("2023-09-30", 40, 5, 8, 1, 4, 8),
            ("2023-06-30", 30, 4, 6, 1, 3, 6),
        ]
        return [
            {
                "period_end_date": date,
                "consolidated": True,
                "revenue_from_operations": str(rev),
                "profit_loss_for_period": str(pat),
                "profit_before_tax": str(pbt),
                "finance_costs": str(fc),
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": str(
                    eps
                ),
                "cash_flows_from_used_in_operating_activities": str(ocf),
                "equity_share_capital": "50000",
                "other_equity": "150000",
                "assets": "500000",
            }
            for date, rev, pat, pbt, fc, eps, ocf in rows
        ]

    def test_ttm_filing_type(self):
        self._setup_db_mock(
            self._make_ttm_quarterly_records()
        )
        self.mock_fetch_price.return_value = {
            "current_price": 2500.0,
            "shares_outstanding": 50000000,
        }
        self.mock_fetch_tech.return_value = {"current_price": 2500.0}

        response = client.get(
            "/financial-metrics?symbol=TEST&country=in&source=nse&filing_type=ttm"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filing_type"] == "ttm"
        assert data["period_end_date"] == "2025-03-31"
        assert data["earnings_per_share"] == pytest.approx(34.0)
        assert data["revenue_growth"] == pytest.approx(88.8889, abs=0.01)
        assert data["earnings_growth"] == pytest.approx(118.1818, abs=0.01)
        assert data["price_to_earnings_ratio"] == pytest.approx(2500.0 / 34.0)
        assert data["net_margin"] == pytest.approx(14.1176, abs=0.01)
        assert data["operating_margin"] == pytest.approx(22.9412, abs=0.01)

    def test_annual_does_not_sum_ttm(self):
        self._setup_db_mock(
            [
                {
                    "period_end_date": "2024-12-31",
                    "consolidated": True,
                    "revenue_from_operations": "100",
                    "profit_loss_for_period": "20",
                    "profit_before_tax": "22",
                    "finance_costs": "2",
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": "10",
                    "cash_flows_from_used_in_operating_activities": "30",
                    "equity_share_capital": "50000",
                    "other_equity": "150000",
                    "assets": "500000",
                },
                {
                    "period_end_date": "2023-12-31",
                    "consolidated": True,
                    "revenue_from_operations": "80",
                    "profit_loss_for_period": "16",
                    "profit_before_tax": "18",
                    "finance_costs": "2",
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": "8",
                    "cash_flows_from_used_in_operating_activities": "20",
                },
                {
                    "period_end_date": "2022-12-31",
                    "consolidated": True,
                    "revenue_from_operations": "60",
                    "profit_loss_for_period": "12",
                    "profit_before_tax": "14",
                    "finance_costs": "2",
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": "6",
                    "cash_flows_from_used_in_operating_activities": "15",
                },
                {
                    "period_end_date": "2021-12-31",
                    "consolidated": True,
                    "revenue_from_operations": "40",
                    "profit_loss_for_period": "8",
                    "profit_before_tax": "10",
                    "finance_costs": "2",
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": "4",
                    "cash_flows_from_used_in_operating_activities": "10",
                },
            ]
        )
        self.mock_fetch_price.return_value = {
            "current_price": 2500.0,
            "shares_outstanding": 50000000,
        }
        self.mock_fetch_tech.return_value = {"current_price": 2500.0}

        response = client.get(
            "/financial-metrics?symbol=TEST&country=in&source=nse&filing_type=annual"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filing_type"] == "annual"
        assert data["earnings_per_share"] == pytest.approx(10.0)
        assert data["price_to_earnings_ratio"] == pytest.approx(250.0)
        assert data["net_margin"] == pytest.approx(20.0)
        assert data["revenue_growth"] == pytest.approx(25.0)

    def test_invalid_filing_type(self):
        self._setup_db_mock([])

        response = client.get(
            "/financial-metrics?symbol=TEST&country=in&source=nse&filing_type=foo"
        )
        assert response.status_code == 400

    def test_interim_quarter_carries_forward_balance_sheet(self):
        """Interim quarters publish P&L-only XBRLs; stock fields must come
        from the nearest older balance sheet instead of nulling out."""
        income, balance, cashflow = [], [], []
        # latest quarter: P&L only, no balance sheet facts at all
        income.append({"period_end_date": "2025-09-30", "consolidated": True,
                       "revenue_from_operations": "100000",
                       "profit_loss_for_period": "15000",
                       "profit_before_tax": "20000", "finance_costs": "3000"})
        cashflow.append({"period_end_date": "2025-09-30", "consolidated": True})
        # prior year-end: full balance sheet
        income.append({"period_end_date": "2025-03-31", "consolidated": True})
        balance.append({"period_end_date": "2025-03-31", "consolidated": True,
                        "equity_share_capital": "50000", "other_equity": "150000",
                        "assets": "500000", "borrowings_current": "20000",
                        "cash_and_cash_equivalents": "10000"})
        cashflow.append({"period_end_date": "2025-03-31", "consolidated": True})

        data_map = {
            "income_statements": income,
            "balance_sheets": balance,
            "cash_flows": [c for c in cashflow if c],
        }
        query_count = {"n": 0}

        def _make_result(items):
            mock_result = MagicMock()
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [self._make_mock_model(d) for d in items]
            mock_result.scalars.return_value = mock_scalars
            return mock_result

        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__exit__ = AsyncMock(return_value=False)
        self.mock_factory.return_value = MagicMock(return_value=mock_cm)

        keys = list(data_map.keys())

        def execute_side_effect(stmt):
            idx = query_count["n"]
            query_count["n"] += 1
            return _make_result(data_map[keys[idx]]) if idx < len(keys) else _make_result([])

        mock_session.execute = AsyncMock(side_effect=execute_side_effect)
        self.mock_fetch_price.return_value = {
            "current_price": 2500.0,
            "shares_outstanding": 50000000,
        }
        self.mock_fetch_tech.return_value = {}

        response = client.get("/financial-metrics?symbol=TEST&country=in&source=nse")
        assert response.status_code == 200
        data = response.json()
        assert data["total_equity"] == 200000
        assert data["book_value_per_share"] == pytest.approx(200000 / 50000000)
        assert data["price_to_book_ratio"] is not None
        assert data["return_on_equity"] == pytest.approx(15000 / 200000 * 100)
        assert data["cash_and_equivalents"] == 10000

    def test_missing_cash_flow_returns_null_not_zero(self):
        self._setup_db_mock(
            [
                {
                    "period_end_date": "2024-12-31",
                    "consolidated": True,
                    "revenue_from_operations": "100000",
                    "profit_loss_for_period": "15000",
                }
            ]
        )
        self.mock_fetch_price.return_value = {"current_price": 2500.0}
        self.mock_fetch_tech.return_value = {}

        response = client.get("/financial-metrics?symbol=TEST&country=in&source=nse")
        data = response.json()
        assert data["free_cash_flow_yield"] is None
        assert data["free_cash_flow_per_share"] is None

    def test_shares_fallback_from_paid_up_and_face_value(self):
        self._setup_db_mock(
            [
                {
                    "period_end_date": "2024-12-31",
                    "consolidated": True,
                    "revenue_from_operations": "100000",
                    "profit_loss_for_period": "15000",
                    "paid_up_value_of_equity_share_capital": "500000",
                    "face_value_of_equity_share_capital": "10",
                    "equity_share_capital": "50000",
                    "other_equity": "150000",
                }
            ]
        )
        # yfinance rate-limited on shares but price is known
        self.mock_fetch_price.return_value = {"current_price": 2500.0}
        self.mock_fetch_tech.return_value = {}

        response = client.get("/financial-metrics?symbol=TEST&country=in&source=nse")
        data = response.json()
        # shares = 500000 / 10 = 50000 -> bvps = 200000 / 50000 = 4
        assert data["price_to_book_ratio"] == pytest.approx(625.0)
        assert data["book_value_per_share"] == pytest.approx(4.0)

    def test_return_ratios_use_ttm_flows_for_quarterly(self):
        records = self._make_ttm_quarterly_records()
        self._setup_db_mock(records)
        self.mock_fetch_price.return_value = {"current_price": 2500.0}
        self.mock_fetch_tech.return_value = {}

        response = client.get("/financial-metrics?symbol=TEST&country=in&source=nse")
        data = response.json()
        ttm_pat = 15 + 13 + 11 + 9  # last four quarters' PAT
        ttm_rev = 100 + 90 + 80 + 70
        equity = (50000 + 150000)
        assert data["return_on_equity"] == pytest.approx(ttm_pat / equity * 100)
        assert data["asset_turnover"] == pytest.approx(ttm_rev / 500000)

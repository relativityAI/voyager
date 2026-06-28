import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from src.ratios.technicals import (
    _generate_yf_symbol,
    _to_valid_float,
    fetch_price_info,
    fetch_technicals,
    get_technicals_catalog,
)


class TestGenerateYfSymbol:
    def test_nse(self):
        assert _generate_yf_symbol("RELIANCE", "NSE") == "RELIANCE.NS"

    def test_bse(self):
        assert _generate_yf_symbol("TCS", "BSE") == "TCS.BO"

    def test_known_exchange_no_suffix(self):
        assert _generate_yf_symbol("AAPL", "NASDAQ") == "AAPL"

    def test_unknown_exchange_uses_code(self):
        assert _generate_yf_symbol("ABC", "LSE") == "ABC.LSE"


class TestToValidFloat:
    def test_none(self):
        assert _to_valid_float(None) is None

    def test_nan(self):
        assert _to_valid_float(float("nan")) is None

    def test_inf(self):
        assert _to_valid_float(float("inf")) is None

    def test_valid(self):
        assert _to_valid_float(42.5) == 42.5

    def test_valid_string(self):
        assert _to_valid_float("42.5") == 42.5


class TestFetchPriceInfo:
    def test_success(self):
        dates = [datetime.now() - timedelta(days=i) for i in range(5)]
        mock_hist = pd.DataFrame(
            {"Close": [100.0, 101.0, 102.0, 103.0, 104.0]},
            index=dates,
        )

        mock_ticker = MagicMock()
        mock_ticker.info = {"sharesOutstanding": 50000000}
        mock_ticker.history.return_value = mock_hist

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, mock_hist)):
            result = fetch_price_info("TEST", "NSE")
            assert result["current_price"] == 104.0
            assert result["shares_outstanding"] == 50000000.0

    def test_empty_history(self):
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker.history.return_value = pd.DataFrame()

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, pd.DataFrame())):
            result = fetch_price_info("TEST", "NSE")
            assert result["current_price"] is None
            assert result["shares_outstanding"] is None

    def test_nan_price_falls_back_to_last_valid(self):
        dates = [datetime.now() - timedelta(days=i) for i in range(5)]
        mock_hist = pd.DataFrame(
            {"Close": [100.0, 101.0, float("nan"), 103.0, float("nan")]},
            index=dates,
        )

        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker.history.return_value = mock_hist

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, mock_hist)):
            result = fetch_price_info("TEST", "NSE")
            assert result["current_price"] == 103.0  # last valid non-NaN close

    def test_all_nan_price_returns_none(self):
        dates = [datetime.now() - timedelta(days=i) for i in range(5)]
        mock_hist = pd.DataFrame(
            {"Close": [float("nan"), float("nan"), float("nan")]},
            index=dates[:3],
        )

        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker.history.return_value = mock_hist

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, mock_hist)):
            result = fetch_price_info("TEST", "NSE")
            assert result["current_price"] is None


class TestFetchTechnicals:
    def _make_hist(self, periods: int = 250, seed: int = 42) -> pd.DataFrame:
        dates = [datetime.now() - timedelta(days=i) for i in range(periods)]
        np.random.seed(seed)
        closes = np.random.randn(periods).cumsum() + 100
        highs = closes + abs(np.random.randn(periods)) * 2
        lows = closes - abs(np.random.randn(periods)) * 2
        volumes = np.random.randint(1000000, 5000000, size=periods)
        return pd.DataFrame(
            {"Close": closes, "High": highs, "Low": lows, "Volume": volumes},
            index=dates[::-1],
        )

    def _clear_caches(self):
        from src.ratios.technicals import _RAW_CACHE, _cache
        _cache.clear()
        _RAW_CACHE.clear()

    def test_success(self):
        self._clear_caches()
        hist = self._make_hist(250, seed=1)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, hist)):
            result = fetch_technicals("TSTSUCC", "NSE")
            assert "current_price" in result
            assert result["current_price"] is not None
            assert isinstance(result["current_price"], float)

    def test_all_indicators_present(self):
        self._clear_caches()
        hist = self._make_hist(250, seed=2)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, hist)):
            result = fetch_technicals("TSTALL", "NSE")
            expected_keys = [
                "current_price", "sma_20", "sma_50", "sma_200",
                "ema_12", "ema_26",
                "rsi_14",
                "macd", "macd_signal", "macd_hist",
                "bb_upper", "bb_middle", "bb_lower",
                "atr_14",
                "stoch_k", "stoch_d",
                "obv",
            ]
            for key in expected_keys:
                assert key in result, f"Missing indicator: {key}"
                assert result[key] is not None, f"None value for: {key}"

    def test_insufficient_data(self):
        self._clear_caches()
        hist = self._make_hist(5, seed=3)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, hist)):
            result = fetch_technicals("TSTINSF", "NSE")
            assert result["current_price"] is not None
            assert "sma_200" not in result
            assert "sma_50" not in result
            assert "rsi_14" not in result

    def test_no_nan_values(self):
        self._clear_caches()
        hist = self._make_hist(250, seed=4)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, hist)):
            result = fetch_technicals("TSTNAN", "NSE")
            for key, val in result.items():
                if isinstance(val, float):
                    assert not math.isnan(val), f"NaN found in {key}"
                    assert not math.isinf(val), f"Inf found in {key}"

    def test_empty_history(self):
        self._clear_caches()
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()

        with patch("src.ratios.technicals._get_yf_raw", return_value=(mock_ticker, pd.DataFrame())):
            result = fetch_technicals("TSTEMPT", "NSE")
            assert "error" in result

    def test_cache_used(self):
        self._clear_caches()
        hist = self._make_hist(250, seed=5)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        call_count = 0

        def raw_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return (mock_ticker, hist)

        with patch("src.ratios.technicals._get_yf_raw", side_effect=raw_side_effect):
            result1 = fetch_technicals("TSTCACH", "NSE")
            result2 = fetch_technicals("TSTCACH", "NSE")
            assert result1 == result2
            assert call_count == 1


class TestGetTechnicalsCatalog:
    def test_returns_dict(self):
        catalog = get_technicals_catalog()
        assert isinstance(catalog, dict)
        assert catalog["id"] == "technicals"

    def test_contains_metrics(self):
        catalog = get_technicals_catalog()
        assert len(catalog["metrics"]) > 0
        ids = [m["id"] for m in catalog["metrics"]]
        assert "current_price" in ids
        assert "rsi_14" in ids
        assert "macd" in ids

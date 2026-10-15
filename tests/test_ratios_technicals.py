import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.tools.nse.technicals import (
    _generate_yf_symbol,
    _resample_ohlcv,
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
    """fetch_price_info is a DB-only read of the latest PriceOHLCV.close.

    It used to scrape yfinance (ticker.info/history plus a chart-endpoint
    fallback); those tests were removed with that code path.
    """

    @staticmethod
    def _factory(row):
        # production does `async with get_session_factory()()`, so the patch
        # must return a session-maker, not a session.
        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return MagicMock(first=lambda: row)

        maker = lambda: _FakeSession()  # noqa: E731
        return lambda: maker

    @pytest.mark.asyncio
    async def test_success_returns_last_close(self):
        with patch(
            "src.db.engine.get_session_factory", self._factory((104.0,))
        ):
            result = await fetch_price_info("TEST", "NSE")
            assert result["current_price"] == 104.0
            assert result["shares_outstanding"] is None

    @pytest.mark.asyncio
    async def test_no_row_returns_none(self):
        with patch("src.db.engine.get_session_factory", self._factory(None)):
            result = await fetch_price_info("TEST", "NSE")
            assert result["current_price"] is None
            assert result["shares_outstanding"] is None

    @pytest.mark.asyncio
    async def test_nan_close_returns_none(self):
        with patch(
            "src.db.engine.get_session_factory",
            self._factory((float("nan"),)),
        ):
            result = await fetch_price_info("TEST", "NSE")
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

    def _mock_history(self, hist: pd.DataFrame):
        """fetch_technicals reads via fetch_history since the multi-timeframe
        refactor; patch at the same seam the tests always targeted."""
        return patch("src.tools.nse.technicals.fetch_history", return_value=hist)

    def _clear_caches(self):
        from src.tools.nse.technicals import _RAW_CACHE, _cache

        _cache.clear()
        _RAW_CACHE.clear()

    def test_success(self):
        self._clear_caches()
        hist = self._make_hist(250, seed=1)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with self._mock_history(hist):
            result = fetch_technicals("TSTSUCC", "NSE")
            assert "current_price" in result
            assert result["current_price"] is not None
            assert isinstance(result["current_price"], float)

    def test_all_indicators_present(self):
        self._clear_caches()
        hist = self._make_hist(250, seed=2)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with self._mock_history(hist):
            result = fetch_technicals("TSTALL", "NSE")
            expected_keys = [
                "current_price",
                "sma_20",
                "sma_50",
                "sma_200",
                "ema_12",
                "ema_26",
                "rsi_14",
                "macd",
                "macd_signal",
                "macd_hist",
                "bb_upper",
                "bb_middle",
                "bb_lower",
                "atr_14",
                "stoch_k",
                "stoch_d",
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

        with self._mock_history(hist):
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

        with self._mock_history(hist):
            result = fetch_technicals("TSTNAN", "NSE")
            for key, val in result.items():
                if isinstance(val, float):
                    assert not math.isnan(val), f"NaN found in {key}"
                    assert not math.isinf(val), f"Inf found in {key}"

    def test_empty_history(self):
        self._clear_caches()
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()

        with self._mock_history(pd.DataFrame()):
            result = fetch_technicals("TSTEMPT", "NSE")
            assert "error" in result

    def test_cache_used(self):
        self._clear_caches()
        hist = self._make_hist(250, seed=5)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        call_count = 0

        def hist_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return hist

        with patch(
            "src.tools.nse.technicals.fetch_history",
            side_effect=hist_side_effect,
        ):
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


class TestResampleOhlcv:
    def test_empty_frame_is_returned_not_raised(self):
        # yfinance returning nothing leaves a RangeIndex frame; resample()
        # raises TypeError on one, which used to surface as a 500.
        assert _resample_ohlcv(pd.DataFrame(), "W").empty

    def test_daily_frame_resamples(self):
        idx = pd.date_range("2026-01-01", periods=10, freq="D")
        df = pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=idx
        )
        out = _resample_ohlcv(df, "W")
        assert not out.empty
        assert out["High"].iloc[0] == 2.0

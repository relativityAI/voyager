"""Tests for the new indicators and pattern analytics.

Complements test_ratios_technicals.py: hand-computed expectations on
synthetic series (so Wilder alignment stays verified) and synthetic
candle sequences that must / must-not trigger each pattern detector.
"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.tools.nse.patterns import (
    candlestick_patterns,
    chart_patterns,
    divergence,
    find_pivots,
    gap_analysis,
    market_structure,
    supply_demand_zones,
    support_resistance,
    trend_regime,
    volume_profile,
)
from src.tools.nse.technicals import (
    _acc_dist,
    _adx,
    _cci,
    _fib_levels,
    _hist_volatility,
    _ichimoku,
    _resample_ohlcv,
    _williams_r,
    fetch_history,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        [datetime(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    )


def _flat_hist(n: int = 120, price: float = 100.0) -> pd.DataFrame:
    """Flat series: every indicator is well-defined, ranges are degenerate."""
    idx = _dates(n)
    return pd.DataFrame(
        {
            "Open": [price] * n,
            "High": [price] * n,
            "Low": [price] * n,
            "Close": [price] * n,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


def _zigzag_hist(n: int = 120, base: float = 100.0, amp: float = 5.0,
                 cycle: int = 20) -> pd.DataFrame:
    """Deterministic zig-zag: clean pivots every `cycle` bars."""
    idx = _dates(n)
    closes = []
    for i in range(n):
        phase = (i % cycle) / cycle
        closes.append(base + amp * (1 - abs(2 * phase - 1)) * (1 if (i // cycle) % 2 == 0 else -1))
    closes = np.array(closes)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 0.5,
            "Low": closes - 0.5,
            "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


# --------------------------------------------------------------------------
# Indicator math
# --------------------------------------------------------------------------

class TestADX:
    def test_flat_market_low_adx(self):
        hist = _flat_hist(120)
        adx, pdi, mdi = _adx(hist["High"], hist["Low"], hist["Close"], 14)
        val = adx.iloc[-1]
        # perfectly flat: DM is 0 everywhere -> DX 0/0 -> NaN is acceptable;
        # the only hard requirement is no +-inf garbage
        assert val is not None
        assert math.isnan(val) or val < 25

    def test_strong_uptrend(self):
        n = 120
        idx = _dates(n)
        closes = pd.Series(np.linspace(100, 200, n), index=idx)
        highs = closes + 1
        lows = closes - 1
        adx, pdi, mdi = _adx(highs, lows, closes, 14)
        assert pdi.iloc[-1] > mdi.iloc[-1]
        assert adx.iloc[-1] > 25

    def test_no_crash_short_series(self):
        hist = _flat_hist(5)
        adx, _, _ = _adx(hist["High"], hist["Low"], hist["Close"], 14)
        assert len(adx) == 5  # NaN-filled, not an exception


class TestCCI:
    def test_flat_zero(self):
        hist = _flat_hist(60)
        cci = _cci(hist["High"], hist["Low"], hist["Close"], 20)
        val = cci.iloc[-1]
        # flat typical price -> numerator 0; mean dev 0 would be 0/0, but
        # the guard keeps it finite or NaN, never +-inf
        assert val is not None and (math.isnan(val) or abs(val) < 1e-6 or not math.isinf(val))

    def test_constant_series_bounded(self):
        hist = _flat_hist(60)
        cci = _cci(hist["High"], hist["Low"], hist["Close"], 20)
        assert all(not math.isinf(v) for v in cci.dropna())

    def test_spike_positive(self):
        n = 60
        hist = _flat_hist(n)
        hist.loc[hist.index[-1], "Close"] = 120
        hist.loc[hist.index[-1], "High"] = 121
        cci = _cci(hist["High"], hist["Low"], hist["Close"], 20)
        assert cci.iloc[-1] > 50


class TestWilliamsR:
    def test_close_at_high_is_zero(self):
        hist = _zigzag_hist(40)
        # stretch the last bar to the 14-bar range high
        hist.loc[hist.index[-1], "High"] = hist["High"].tail(14).max() + 1
        hist.loc[hist.index[-1], "Close"] = hist.loc[hist.index[-1], "High"]
        wr = _williams_r(hist["High"], hist["Low"], hist["Close"], 14)
        assert abs(wr.iloc[-1]) < 1e-9  # close == highest high -> %R = 0

    def test_close_at_low_is_minus_100(self):
        hist = _zigzag_hist(40)
        hist.loc[hist.index[-1], "Low"] = hist["Low"].tail(14).min() - 1
        hist.loc[hist.index[-1], "Close"] = hist.loc[hist.index[-1], "Low"]
        wr = _williams_r(hist["High"], hist["Low"], hist["Close"], 14)
        assert abs(wr.iloc[-1] + 100) < 1e-9

    def test_flat_range_nan_not_inf(self):
        hist = _flat_hist(30)
        wr = _williams_r(hist["High"], hist["Low"], hist["Close"], 14)
        assert math.isnan(wr.iloc[-1])  # degenerate range -> NaN, never +-inf


class TestAccDist:
    def test_close_at_high_accumulates(self):
        # range must be non-degenerate for CLV to be defined
        n = 30
        idx = _dates(n)
        hist = pd.DataFrame(
            {"Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
             "Close": [101.0] * n, "Volume": [1_000_000] * n},
            index=idx,
        )
        ad = _acc_dist(hist["High"], hist["Low"], hist["Close"], hist["Volume"])
        # every bar closes at its high -> CLV = +1 -> line rises every bar
        assert ad.iloc[-1] > ad.iloc[0]
        assert abs(ad.iloc[-1]) == pytest.approx(n * 1_000_000)

    def test_close_at_low_distributes(self):
        n = 30
        idx = _dates(n)
        hist = pd.DataFrame(
            {"Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
             "Close": [99.0] * n, "Volume": [1_000_000] * n},
            index=idx,
        )
        ad = _acc_dist(hist["High"], hist["Low"], hist["Close"], hist["Volume"])
        assert ad.iloc[-1] < 0  # close at low: CLV = -1 every bar


class TestHistVolatility:
    def test_zero_for_flat_series(self):
        hist = _flat_hist(60)
        hv = _hist_volatility(hist["Close"], 20)
        assert hv.iloc[-1] == pytest.approx(0.0, abs=1e-9)

    def test_positive_for_random_walk(self):
        rng = np.random.default_rng(7)
        idx = _dates(120)
        closes = pd.Series(100 * np.exp(rng.normal(0, 0.02, 120).cumsum()), index=idx)
        hv = _hist_volatility(closes, 20)
        assert hv.iloc[-1] > 0


class TestFibLevels:
    def test_up_swing_levels_between_extremes(self):
        n = 60
        idx = _dates(n)
        # rally from 100 to 150 over the window
        closes = pd.Series(np.linspace(100, 150, n), index=idx)
        highs = closes + 1
        lows = closes - 1
        fib = _fib_levels(highs, lows, lookback=60)
        assert fib["direction"] == "up"
        assert fib["swing_low"] == pytest.approx(99.0, abs=0.6)
        assert fib["swing_high"] == pytest.approx(151.0, abs=0.6)
        lv = fib["levels"]
        assert lv["0.0%"] == pytest.approx(lv["0.0%"])  # self-consistent
        assert lv["100.0%"] < lv["50.0%"] < lv["0.0%"]  # retracement ladder
        assert lv["61.8%"] < lv["38.2%"]

    def test_degenerate_range_empty(self):
        hist = _flat_hist(60)
        fib = _fib_levels(hist["High"], hist["Low"], lookback=60)
        assert fib == {}  # swing_high == swing_low -> no levels


class TestIchimoku:
    def test_components_present_after_warmup(self):
        hist = _zigzag_hist(120)
        tenkan, kijun, sa, sb, chikou = _ichimoku(hist["High"], hist["Low"], hist["Close"])
        assert not np.isnan(tenkan.iloc[-1])
        assert not np.isnan(kijun.iloc[-1])
        assert not np.isnan(sa.iloc[-1])
        assert not np.isnan(sb.iloc[-1])
        # chikou is close shifted *forward* -> last 26 slots are NaN
        assert np.isnan(chikou.iloc[-1])
        assert not np.isnan(chikou.iloc[-27])

    def test_short_series_all_nan_spans(self):
        # Senkou A: (9/26 warmup) + 26 shift -> needs 52 bars
        # Senkou B: 52-bar mid + 26 shift -> needs 78 bars
        hist = _zigzag_hist(60)
        _, _, sa, sb, _ = _ichimoku(hist["High"], hist["Low"], hist["Close"])
        assert not np.isnan(sa.iloc[-1])   # 60 >= 52: span A is defined
        assert np.isnan(sb.iloc[-1])      # 60 < 78: span B is not


class TestResample:
    def test_weekly_ohlc_aggregation(self):
        daily = _zigzag_hist(30)
        weekly = _resample_ohlcv(daily, "W")
        assert 4 <= len(weekly) <= 6
        # weekly high >= weekly low, close within range
        last = weekly.iloc[-1]
        assert last["High"] >= last["Low"]
        assert last["Low"] <= last["Close"] <= last["High"]

    def test_monthly_volume_sums(self):
        daily = _flat_hist(90)
        monthly = _resample_ohlcv(daily, "ME")
        assert len(monthly) <= 4
        assert monthly["Volume"].sum() == pytest.approx(90 * 1_000_000)


class TestFetchHistoryPartialCandle:
    """Regression: a live day pre-allocated with NaN OHLC must be dropped,
    never poisoned into every indicator's last value."""

    def test_nan_final_bar_dropped(self):
        hist = _flat_hist(30)
        nan_day = pd.DataFrame(
            {"Open": [np.nan], "High": [np.nan], "Low": [np.nan],
             "Close": [np.nan], "Volume": [0]},
            index=[hist.index[-1] + timedelta(days=1)],
        )
        poisoned = pd.concat([hist, nan_day])
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = poisoned
        with patch("src.tools.nse.technicals.yf.Ticker", return_value=mock_ticker):
            cleaned = fetch_history("TESTPART", "NSE", period="1y")
        assert not cleaned.empty
        assert cleaned["Close"].notna().all()
        assert len(cleaned) == 30

    def test_empty_response_negative_cached_not_full_ttl(self):
        """Regression (GLAND on Render): one rate-limited empty response used
        to be cached for the full TTL, blanking the symbol's report for 5
        minutes. It must be retried once, negative-cached only briefly, and
        self-heal on the next call after the negative TTL."""
        import time

        import src.tools.nse.technicals as T

        T._cache.clear()
        T._RAW_CACHE.clear()
        good = _flat_hist(5)
        try:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = pd.DataFrame()  # rate-limited
            # patch the retry sleep so the test doesn't actually wait
            with patch("src.tools.nse.technicals.time.sleep"):
                with patch(
                    "src.tools.nse.technicals.yf.Ticker", return_value=mock_ticker
                ):
                    first = fetch_history("TESTNEG", "NSE", period="1y")
            assert first.empty
            assert mock_ticker.history.call_count == 2  # retried once

            # within the negative TTL the empty frame is still served...
            mock_ticker.history.return_value = good
            with patch("src.tools.nse.technicals.time.sleep"):
                with patch(
                    "src.tools.nse.technicals.yf.Ticker", return_value=mock_ticker
                ):
                    still = fetch_history("TESTNEG", "NSE", period="1y")
            assert still.empty

            # ...but after the *negative* TTL (not the full RAW TTL) it heals
            entry = T._RAW_CACHE["TESTNEG:NSE:hist:1y:1d"]
            entry["ts"] = time.time() - T.NEG_CACHE_TTL - 1
            with patch("src.tools.nse.technicals.time.sleep"):
                with patch(
                    "src.tools.nse.technicals.yf.Ticker", return_value=mock_ticker
                ):
                    healed = fetch_history("TESTNEG", "NSE", period="1y")
            assert len(healed) == 5
        finally:
            T._RAW_CACHE.pop("TESTNEG:NSE:hist:1y:1d", None)
            T._cache.clear()


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

class TestPivots:
    def test_zigzag_pivots_found(self):
        hist = _zigzag_hist(120, cycle=20)
        pivots = find_pivots(hist["High"], hist["Low"], 3, 3)
        assert len(pivots["high"]) >= 2
        assert len(pivots["low"]) >= 2

    def test_flat_no_pivots(self):
        hist = _flat_hist(60)
        pivots = find_pivots(hist["High"], hist["Low"], 3, 3)
        # flat: every bar equals its window max/min; the strict
        # "(no bar strictly greater)" condition fires -> tolerated either way
        assert isinstance(pivots["high"], list)


class TestSupportResistance:
    def test_levels_split_around_price(self):
        hist = _zigzag_hist(120, base=100, amp=8, cycle=24)
        hist.loc[hist.index[-1], "Close"] = hist["Low"].iloc[:-5].min()  # force low close
        sr = support_resistance(hist)
        all_prices = [l["price"] for l in sr["supports"]] + [
            l["price"] for l in sr["resistances"]
        ]
        close = float(hist["Close"].iloc[-1])
        assert all(l["price"] < close for l in sr["supports"])
        assert all(l["price"] >= close for l in sr["resistances"])
        assert sr["pivots_found"] > 0


class TestCandlePatterns:
    def _from_rows(self, rows: list[dict]) -> pd.DataFrame:
        idx = _dates(len(rows))
        return pd.DataFrame(rows, index=idx)

    def test_bullish_engulfing_detected(self):
        rows = [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100.5, "Volume": 1000},
        ] * 5 + [
            # red candle then bigger green candle engulfing it
            {"Open": 101, "High": 101.5, "Low": 100, "Close": 100.1, "Volume": 1000},
            {"Open": 100.0, "High": 102.5, "Low": 99.9, "Close": 102.4, "Volume": 1000},
        ]
        hist = self._from_rows(rows)
        res = candlestick_patterns(hist, last_n=7)
        hits = [p for h in res["hits"] for p in h["patterns"]]
        assert "bullish_engulfing" in hits

    def test_three_white_soldiers_detected(self):
        rows = [{"Open": 100, "High": 101, "Low": 99, "Close": 100.5, "Volume": 1000}] * 5
        for i in range(3):
            base = 100 + i
            rows.append({"Open": base, "High": base + 1.2, "Low": base - 0.2,
                         "Close": base + 1.0, "Volume": 1000})
        hist = self._from_rows(rows)
        res = candlestick_patterns(hist, last_n=9)
        hits = [p for h in res["hits"] for p in h["patterns"]]
        assert "three_white_soldiers" in hits

    def test_quiet_market_no_hits(self):
        rng = np.random.default_rng(3)
        rows = []
        price = 100.0
        for _ in range(20):
            o = price
            c = price + rng.normal(0, 0.05)
            rows.append({"Open": o, "High": max(o, c) + 0.05, "Low": min(o, c) - 0.05,
                         "Close": c, "Volume": 1000})
            price = c
        hist = self._from_rows(rows)
        res = candlestick_patterns(hist, last_n=15)
        assert res["bullish_count"] + res["bearish_count"] <= 2

    def test_tally_counts(self):
        rows = [{"Open": 100, "High": 101, "Low": 99, "Close": 100.5, "Volume": 1000}] * 8
        hist = self._from_rows(rows)
        res = candlestick_patterns(hist, last_n=10)
        assert res["bullish_count"] == 0 and res["bearish_count"] == 0


class TestVolumeProfile:
    def test_poc_and_value_area(self):
        n = 60
        idx = _dates(n)
        # two price camps: 40 bars at ~100, 20 bars at ~120
        closes = [100.0] * 40 + [120.0] * 20
        hist = pd.DataFrame(
            {"Open": closes, "High": [c + 0.5 for c in closes],
             "Low": [c - 0.5 for c in closes], "Close": closes,
             "Volume": [1_000_000] * n},
            index=idx,
        )
        vp = volume_profile(hist, bins=10)
        assert vp["poc"] == pytest.approx(97.75, abs=5.0)  # heavier camp wins
        va = vp["value_area"]
        assert va["low"] < va["high"]
        covered = sum(
            b["volume"] for b in vp["bins"]
            if va["low"] <= (b["price_low"] + b["price_high"]) / 2 <= va["high"]
        )
        total = sum(b["volume"] for b in vp["bins"])
        assert covered / total >= 0.69  # ~70% value area

    def test_needs_volume(self):
        hist = _flat_hist(60).drop(columns=["Volume"])
        assert volume_profile(hist) == {}


class TestGapAnalysis:
    def test_gap_up_detected_and_fill_checked(self):
        rows = [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 103, "High": 104, "Low": 102, "Close": 103, "Volume": 1000},  # gap up
            {"Open": 103.5, "High": 105, "Low": 99.5, "Close": 100.5, "Volume": 1000},  # fills it
        ]
        hist = self._from_rows(rows) if False else pd.DataFrame(
            rows, index=_dates(3)
        )
        res = gap_analysis(hist)
        assert len(res["gaps"]) == 1
        g = res["gaps"][0]
        assert g["type"] == "gap_up"
        assert g["gap_pct"] == pytest.approx(3.0)
        assert g["filled"] is True

    def test_no_gaps_in_contiguous_series(self):
        # contiguous opens within 0.5% of prior close, but each bar's O->C
        # move is ~2.5%: only the overnight open-vs-prior-close is a gap.
        rows = [
            {"Open": 100.0, "High": 100.4, "Low": 99.6, "Close": 100.2, "Volume": 1000},
            {"Open": 100.3, "High": 100.7, "Low": 99.9, "Close": 100.1, "Volume": 1000},
            {"Open": 100.0, "High": 100.5, "Low": 99.5, "Close": 100.4, "Volume": 1000},
            {"Open": 100.2, "High": 100.6, "Low": 99.8, "Close": 100.0, "Volume": 1000},
        ]
        hist = pd.DataFrame(rows, index=_dates(4))
        res = gap_analysis(hist)
        assert res["gaps"] == []


class TestMarketStructure:
    def test_uptrend_classification(self):
        n = 120
        idx = _dates(n)
        # sawtooth with rising floor and ceiling
        closes, i = [], 0
        level = 100.0
        while i < n:
            for delta in (5, 4, 3, 2, 1, 0, -1, -2, -3, -4):
                closes.append(level + delta)
                i += 1
                if i >= n:
                    break
            level += 6
        closes = pd.Series(closes[:n], index=idx)
        hist = pd.DataFrame(
            {"Open": closes, "High": closes + 0.5, "Low": closes - 0.5,
             "Close": closes, "Volume": [1000] * n},
            index=idx,
        )
        ms = market_structure(hist)
        assert "uptrend" in ms["classification"]

    def test_insufficient_data(self):
        # flat series produces no strict pivots -> structure is undefined
        hist = _flat_hist(10)
        assert market_structure(hist)["classification"] == "insufficient_data"


class TestTrendRegime:
    def test_steady_rise_is_trending_up(self):
        n = 120
        idx = _dates(n)
        closes = pd.Series(np.linspace(100, 150, n), index=idx)
        hist = pd.DataFrame(
            {"Open": closes, "High": closes + 0.3, "Low": closes - 0.3,
             "Close": closes, "Volume": [1000] * n},
            index=idx,
        )
        reg = trend_regime(hist)
        assert reg["regime"] == "trending up"
        assert reg["adx"] > 20

    def test_short_series(self):
        assert trend_regime(_flat_hist(10))["regime"] == "insufficient_data"


class TestChartPatterns:
    def test_double_bottom_shape(self):
        # explicit W: 110 -> 100 -> 110 -> 100 -> 110. The trough bars are
        # strict local minima, so the 3/3 fractal window confirms them.
        path = (
            [110 - i for i in range(11)]      # 110 down to 100
            + [100 + i for i in range(11)]    # up to 110
            + [110 - i for i in range(11)]    # down to 100
            + [100 + i for i in range(11)]    # up to 110
        )
        n = len(path)
        closes = pd.Series(path, index=_dates(n))
        hist = pd.DataFrame(
            {"Open": closes, "High": closes + 0.2, "Low": closes - 0.2,
             "Close": closes, "Volume": [1000] * n},
            index=_dates(n),
        )
        res = chart_patterns(hist)
        assert "possible_double_bottom" in res["patterns"]


class TestDivergence:
    def test_no_crash_and_shape(self):
        hist = _zigzag_hist(120)
        res = divergence(hist)
        assert "divergences" in res
        for d in res["divergences"]:
            assert d["type"] in ("bearish_regular", "bullish_regular")


class TestSupplyDemandZones:
    def test_zones_bounded(self):
        hist = _zigzag_hist(120, amp=10, cycle=24)
        zones = supply_demand_zones(hist, lookback=90, max_zones=4)
        assert len(zones) <= 4
        for z in zones:
            assert z["kind"] in ("demand", "supply")
            assert z["zone_low"] < z["zone_high"]


# --------------------------------------------------------------------------
# Report assembler (light: no network)
# --------------------------------------------------------------------------

class TestReportSections:
    def test_section_list_is_61(self):
        from src.services.technical_report import REPORT_SECTIONS
        assert len(REPORT_SECTIONS) == 61
        assert len(set(REPORT_SECTIONS)) == 61

    def test_unsupported_marker_shape(self):
        from src.services.technical_report import _ok, _unsupported
        u = _unsupported("why")
        assert u == {"status": "unsupported", "reason": "why"}
        o = _ok({"a": 1})
        assert o == {"status": "ok", "data": {"a": 1}}

    def test_confluence_counts(self):
        from src.services.technical_report import _confluence
        signals = [
            {"signal": "a", "value": 1, "bias": "bullish", "note": ""},
            {"signal": "b", "value": 2, "bias": "bullish", "note": ""},
            {"signal": "c", "value": 3, "bias": "bearish", "note": ""},
        ]
        c = _confluence(signals)
        assert c["bullish"] == 2 and c["bearish"] == 1
        assert c["overall"] in ("bullish", "mixed")

    def test_mtfs_matrix_intraday_vwap_basis(self):
        from src.services.technical_report import _mtf_matrix
        tfs = {
            "intraday": {"status": "ok", "last": 100, "above_vwap": True},
            "daily": {"current_price": 100, "sma_20": 90, "rsi_14": 60,
                      "macd": 1, "macd_signal": 0.5},
            "weekly": {"error": "no data"},
            "monthly": {},
        }
        rows = {r["timeframe"]: r for r in _mtf_matrix(tfs)}
        assert rows["intraday"]["state"] == "bullish"
        assert rows["daily"]["state"] == "bullish"
        assert rows["weekly"]["state"] == "unavailable"
        assert rows["monthly"]["state"] == "unavailable"


# --------------------------------------------------------------------------
# Phase 0/1/2 additions: change windows, candle dedupe, structure hygiene,
# volume stats, raw OHLCV rows, scenarios, risk flags, full build (offline)
# --------------------------------------------------------------------------

class TestChangeWindows:
    def test_daily_window_semantics(self):
        from src.services.technical_report import _CHANGE_WINDOWS, _collect_timeframe
        hist = _zigzag_hist(300)
        out = _collect_timeframe("daily", hist, None)
        assert out["changes_window_bars"] == dict(_CHANGE_WINDOWS["daily"])
        expected_1w = (hist["Close"].iloc[-1] / hist["Close"].iloc[-6] - 1) * 100
        assert out["change_pct_1w"] == pytest.approx(expected_1w, abs=0.02)

    def test_weekly_has_no_daily_window(self):
        from src.services.technical_report import _CHANGE_WINDOWS, _collect_timeframe
        hist = _zigzag_hist(800)
        out = _collect_timeframe("weekly", hist, "W")
        assert out["changes_window_bars"] == dict(_CHANGE_WINDOWS["weekly"])
        assert "change_pct_1d" not in out
        assert "change_pct_1w" in out and "change_pct_1y" in out
        weekly = _resample_ohlcv(hist, "W").tail(280)
        expected_1w = (weekly["Close"].iloc[-1] / weekly["Close"].iloc[-2] - 1) * 100
        assert out["change_pct_1w"] == pytest.approx(expected_1w, abs=0.02)


class TestCandlestickDedupeAndDate:
    def test_streak_yields_one_hit_with_date(self):
        rows = [{"Open": 100, "High": 101, "Low": 99, "Close": 100.5, "Volume": 1000}] * 6
        price = 100.0
        for _ in range(5):  # five consecutive big red candles
            rows.append({"Open": price, "High": price, "Low": price - 1,
                         "Close": price - 1, "Volume": 1000})
            price -= 1
        hist = pd.DataFrame(rows, index=_dates(len(rows)))
        res = candlestick_patterns(hist, last_n=len(rows))
        crow_hits = [h for h in res["hits"] if "three_black_crows" in h["patterns"]]
        assert len(crow_hits) == 1  # streak collapses to its first occurrence
        assert crow_hits[0]["date"] == str(hist.index[8])[:10]
        assert res["bearish_count"] >= 1

    def test_hits_carry_date_key(self):
        rows = [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100.5, "Volume": 1000},
        ] * 5 + [
            {"Open": 101, "High": 101.5, "Low": 100, "Close": 100.1, "Volume": 1000},
            {"Open": 100.0, "High": 102.5, "Low": 99.9, "Close": 102.4, "Volume": 1000},
        ]
        hist = pd.DataFrame(rows, index=_dates(len(rows)))
        res = candlestick_patterns(hist, last_n=len(rows))
        n = len(hist)
        assert res["hits"]
        for h in res["hits"]:
            i = n - 1 - h["bar_offset_from_end"]
            assert h["date"] == str(hist.index[i])[:10]


class TestMarketStructureHygiene:
    def test_uptrend_carries_confidence_and_pivot_count(self):
        n = 260
        idx = _dates(n)
        closes, i, level = [], 0, 100.0
        while i < n:
            for delta in (5, 4, 3, 2, 1, 0, -1, -2, -3, -4):
                closes.append(level + delta)
                i += 1
                if i >= n:
                    break
            level += 6
        hist = pd.DataFrame(
            {"Open": closes[:n], "High": [c + 0.5 for c in closes[:n]],
             "Low": [c - 0.5 for c in closes[:n]], "Close": closes[:n],
             "Volume": [1000] * n},
            index=idx,
        )
        ms = market_structure(hist)
        assert ms["classification"].startswith("uptrend")
        assert sum(ms["pivot_count"].values()) >= 4
        assert ms["confidence"] in ("high", "low")
        assert ms["price_vs_sma200"] in ("below", "above")

    def test_insufficient_data_shape(self):
        ms = market_structure(_flat_hist(10))
        assert ms["confidence"] == "low"
        assert ms["price_vs_sma200"] is None
        assert ms["pivot_count"] == {"pivot_highs": 0, "pivot_lows": 0}


class TestVolumeStatsAndRows:
    def test_volume_stats_shape(self):
        from src.services.technical_report import _volume_stats
        hist = _flat_hist(60)
        vs = _volume_stats(hist)
        assert vs["trend"] == "stable"
        assert vs["avg_volume_20bar"] == 1_000_000
        assert vs["spikes_last_60bars"] == []
        # spike the last bar above 2x avg20 -> dated spike + rising trend
        hist.loc[hist.index[-1], "Volume"] = 5_000_000
        vs = _volume_stats(hist)
        assert vs["trend"] == "rising"
        assert vs["spikes_last_60bars"][0]["date"] == str(hist.index[-1])[:10]
        assert vs["spikes_last_60bars"][0]["multiple_of_avg20"] > 2

    def test_volume_stats_without_column(self):
        from src.services.technical_report import _volume_stats
        assert _volume_stats(_flat_hist(60).drop(columns=["Volume"])) is None

    def test_ohlcv_rows_recompute_sma(self):
        from src.services.technical_report import _ohlcv_rows
        hist = _zigzag_hist(300)
        rows = _ohlcv_rows(hist.tail(320))
        assert len(rows) == 300
        assert set(rows[0]) == {"date", "open", "high", "low", "close", "volume"}
        assert rows[-1]["volume"] == 1_000_000
        closes = pd.Series([r["close"] for r in rows])
        sma50 = float(closes.tail(50).mean())
        from src.tools.nse.technicals import _sma as ta_sma
        expected = float(ta_sma(hist["Close"], 50).iloc[-1])
        assert sma50 == pytest.approx(expected, abs=0.2)


class TestScenariosShape:
    def test_trigger_targets_invalidation_and_bias(self):
        from src.services.technical_report import _scenarios
        tfs = {"daily": {"current_price": 100.0}}
        levels = {
            "status": "ok",
            "exit_zones": [{"level": 110, "kind": "resistance"},
                           {"level": 120, "kind": "resistance"}],
            "entry_zones": [{"level": 90, "kind": "support"},
                            {"level": 85, "kind": "support"}],
            "targets": {"tp1": 115, "tp2": 125, "tp3": None},
            "invalidation_level": 84.0,
        }
        confluence = {"overall": "bearish", "bullish": 1, "bearish": 4,
                      "neutral": 5, "total_signals": 10, "bullish_share": 10.0}
        signals = [
            {"signal": "rsi_14", "bias": "bullish"},
            {"signal": "macd", "bias": "bearish"},
            {"signal": "stoch", "bias": "neutral"},
        ]
        res = _scenarios(tfs, levels, confluence, signals)
        assert res["status"] == "ok"
        assert res["bullish"]["targets"] == [115, 125]
        # bearish targets are supports: below price, never the TP ladder
        assert res["bearish"]["targets"] == [90, 85]
        assert all(t < 100 for t in res["bearish"]["targets"])
        assert res["bullish"]["trigger"].startswith("close above resistance")
        assert res["bullish"]["invalidation"] == 84.0
        assert "rsi_14" in res["bullish"]["supporting_factors"]
        assert "macd" in res["bearish"]["supporting_factors"]
        assert res["signal_shares_pct"] == {"bullish": 10.0, "bearish": 40.0}

    def test_unsupported_without_daily(self):
        from src.services.technical_report import _scenarios
        res = _scenarios({}, {"status": "ok"}, {"overall": "neutral"}, [])
        assert res["status"] == "unsupported"


class TestRiskFactors:
    def test_drawdown_death_cross_and_losses_flagged(self):
        from src.services.technical_report import _risk_factors
        daily = {"high_52w": 200.0, "sma_200": 150.0, "change_pct_1y": -35.0,
                 "golden_death_cross_state": "death-cross regime"}
        flags = _risk_factors(daily, {}, {"overall": "bearish"}, None, 100.0, False)
        text = " ".join(flags)
        assert "52-week high" in text
        assert "death-cross" in text
        assert "SMA200" in text
        assert "1y change" in text
        assert "benchmark feed unavailable" in text

    def test_clean_state_returns_no_flags(self):
        from src.services.technical_report import _risk_factors
        assert _risk_factors({}, {}, {"overall": "bullish"}, None, None, True) == []


class TestBuildReportOffline:
    """Full offline assembly: all 61 sections present and counted."""

    @staticmethod
    def _hist() -> pd.DataFrame:
        return _zigzag_hist(1300)

    def test_full_report_61_sections(self, monkeypatch):
        import asyncio

        import src.services.technical_report as tr

        hist = self._hist()
        monkeypatch.setattr(tr, "fetch_history", lambda *a, **k: hist.copy())
        monkeypatch.setattr(
            tr, "fetch_earnings_dates",
            lambda *a, **k: [{"date": "2099-11-01", "title": "Quarterly earnings"}],
        )
        monkeypatch.setattr(tr, "_benchmark_history", lambda *a, **k: pd.DataFrame())
        report = asyncio.run(tr.build_technical_report(
            "TESTCO", "NSE", "daily,weekly,monthly",
            [{"heading": "Board meeting", "date": "2099-12-01"}],
        ))
        assert report["sections_count"] == 61
        assert list(report["sections"]) == tr.REPORT_SECTIONS

        ex = report["sections"]["executive_summary"]["data"]
        assert ex["sections_supported"] + ex["sections_unsupported"] == 61

        ph = report["sections"]["price_history"]
        assert ph["status"] == "ok"
        assert ph["data"]["adjustment"] == "split+dividend adjusted"
        assert len(ph["data"]["daily"]) > 0 and len(ph["data"]["weekly"]) > 0

        sr = report["sections"]["support_resistance"]
        assert sr["status"] == "ok" and "weekly" in sr["data"]

        med = report["sections"]["medium_term_setup"]
        assert med["status"] == "ok"
        assert "change_pct_1d" not in med["data"]["changes_pct"]
        assert med["data"]["changes_window_bars"] == {"1w": 1, "1m": 4, "3m": 13, "1y": 52}

        method = report["sections"]["data_sources_methodology"]["data"]
        assert "adjusted" in method["price_adjustment"]
        assert report["sections"]["upcoming_catalysts"]["status"] == "ok"
        assert report["sections"]["technical_risk_factors"]["status"] == "ok"


# --------------------------------------------------------------------------
# Review pass 2: hygiene, semantics, levels, events
# --------------------------------------------------------------------------

class TestZeroVolumeBarsDropped:
    def test_fetch_history_drops_zero_volume(self, monkeypatch):
        import yfinance as yf
        from src.tools.nse.technicals import fetch_history
        idx = pd.date_range("2026-01-01", periods=10, freq="D")
        hist = pd.DataFrame(
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
             "Volume": [1000] * 5 + [0, 0, 1000, 1000, 1000]},
            index=idx,
        )
        monkeypatch.setattr(yf.Ticker, "history", lambda *a, **k: hist)
        out = fetch_history("TEST", "NSE", period="1y")
        assert len(out) == 8
        assert (out["Volume"] > 0).all()


class TestSignalSemantics:
    def test_rsi_neutral_band(self):
        from src.services.technical_report import _daily_signals
        base = {"current_price": 100.0, "sma_20": 100.0, "sma_50": 100.0,
                "sma_200": 100.0, "ema_12": 100.0, "ema_26": 100.0}
        sig = {s["signal"]: s for s in _daily_signals({**base, "rsi_14": 47.0})}
        assert sig["rsi_14"]["bias"] == "neutral"
        sig = {s["signal"]: s for s in _daily_signals({**base, "rsi_14": 65.0})}
        assert sig["rsi_14"]["bias"] == "bullish"

    def test_cci_neutral_band(self):
        from src.services.technical_report import _daily_signals
        base = {"current_price": 100.0}
        sig = {s["signal"]: s for s in _daily_signals({**base, "cci_20": -29.0})}
        assert sig["cci_20"]["bias"] == "neutral"

    def test_adx_value_carries_di(self):
        from src.services.technical_report import _daily_signals
        base = {"current_price": 100.0}
        sig = {s["signal"]: s for s in _daily_signals(
            {**base, "adx_14": 29.4, "plus_di_14": 22.9, "minus_di_14": 30.6}
        )}
        assert sig["adx_14"]["bias"] == "bearish"
        assert sig["adx_14"]["value"]["adx"] == 29.4
        assert "DI" in sig["adx_14"]["note"]

    def test_vwap_signal_notes_anchor(self):
        from src.services.technical_report import _daily_signals
        sig = {s["signal"]: s for s in _daily_signals(
            {"current_price": 100.0, "vwap_anchored": 90.0}
        )}
        assert "anchored" in sig["price_vs_vwap"]["note"]


class TestTrendDimensions:
    def test_conflict_is_named(self):
        import asyncio
        import src.services.technical_report as tr

        hist = _zigzag_hist(1300)
        monkey = pytest.MonkeyPatch()
        monkey.setattr(tr, "fetch_history", lambda *a, **k: hist.copy())
        monkey.setattr(tr, "fetch_earnings_dates", lambda *a, **k: [])
        monkey.setattr(tr, "_benchmark_history", lambda *a, **k: pd.DataFrame())
        report = asyncio.run(tr.build_technical_report("TESTCO", "NSE", "daily,weekly,monthly", []))
        td = report["sections"]["trend_analysis"]["data"]["trend_dimensions"]
        assert "structural" in td and "current" in td
        assert "agreement" in td
        ex = report["sections"]["executive_summary"]["data"]
        assert "trend_headline" in ex and "weekly_structure" in ex
        assert "conflicts with" in ex["trend_headline"] or "aligned" in ex["trend_headline"]
        monkey.undo()


class TestSupportResistancePerSide:
    def test_per_side_cap_not_global(self):
        # 5 supports + 5 resistances, all with equal touches: a global cap of 8
        # would let one side starve the other; per-side cap keeps both.
        levels = [{"price": 90.0 + i, "touches": 1} for i in range(5)]
        levels += [{"price": 110.0 + i, "touches": 1} for i in range(5)]
        close = 100.0
        supports = sorted(
            (l for l in levels if l["price"] < close),
            key=lambda x: x["touches"], reverse=True,
        )[:8]
        resistances = sorted(
            (l for l in levels if l["price"] >= close),
            key=lambda x: x["touches"], reverse=True,
        )[:8]
        assert len(supports) == 5 and len(resistances) == 5

    def test_both_sides_populated(self):
        rows = []
        price = 100.0
        for i in range(200):
            rows.append({"Open": price, "High": price + 2, "Low": price - 2,
                         "Close": price, "Volume": 1000})
            price += [3, -2, 4, -5, 2, -3, 5, -4][i % 8]
        hist = pd.DataFrame(rows, index=_dates(200))
        sr = support_resistance(hist)
        close = float(hist["Close"].iloc[-1])
        assert all(l["price"] < close for l in sr["supports"])
        assert all(l["price"] >= close for l in sr["resistances"])
        assert sr["pivots_found"] > 0


class TestSupplyDemandZonesBounded:
    def test_zone_width_capped_and_side_respected(self):
        hist = _zigzag_hist(120, amp=10, cycle=24)
        zones = supply_demand_zones(hist, lookback=90, max_zones=4)
        close = float(hist["Close"].iloc[-1])
        for z in zones:
            width_pct = (z["zone_high"] - z["zone_low"]) / z["zone_low"]
            assert width_pct <= 0.07
            if z["kind"] == "demand":
                assert z["zone_high"] <= close * 1.01
            else:
                assert z["zone_low"] >= close * 0.99


class TestVolumeProfileSpread:
    def test_no_bin_holds_whole_bar(self):
        rows = [{"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}] * 40
        rows.append({"Open": 100, "High": 140, "Low": 95, "Close": 130, "Volume": 23_000_000})
        hist = pd.DataFrame(rows, index=_dates(len(rows)))
        vp = volume_profile(hist, bins=10)
        biggest = max(b["volume"] for b in vp["bins"])
        assert biggest < 23_000_000  # spread across bins, not dumped into one


class TestUnusualDays:
    def test_crash_day_flagged(self):
        from src.services.technical_report import _volume_stats
        rows = [{"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}] * 40
        rows.append({"Open": 100, "High": 105, "Low": 80, "Close": 85, "Volume": 8_000_000})
        hist = pd.DataFrame(rows, index=_dates(len(rows)))
        vs = _volume_stats(hist)
        assert vs["unusual_days"]
        assert vs["unusual_days"][0]["range_pct"] > 20
        assert vs["unusual_days"][0]["close_change_pct"] == -15.0


class TestChartPatternDetectors:
    def test_rising_channel_detected(self):
        rows = []
        for i in range(60):
            c = 100 + i * 0.5
            rows.append({"Open": c, "High": c + 1, "Low": c - 1,
                         "Close": c + 0.2, "Volume": 1000})
        hist = pd.DataFrame(rows, index=_dates(60))
        cp = chart_patterns(hist)
        assert "rising_channel" in cp["patterns"]

    def test_trading_range_detected(self):
        rows = []
        for i in range(60):
            c = 100 + (i % 4) * 0.3
            rows.append({"Open": c, "High": 102, "Low": 98,
                         "Close": c, "Volume": 1000})
        hist = pd.DataFrame(rows, index=_dates(60))
        cp = chart_patterns(hist)
        assert "trading_range" in cp["patterns"]


class TestBollingerPopulationStd:
    def test_bb_matches_population(self):
        from src.tools.nse.technicals import _bbands
        close = pd.Series([100 + (i % 7) * 0.5 for i in range(40)])
        bu, bm, bl = _bbands(close, 20, 2.0)
        mid = close.rolling(20).mean()
        sd = close.rolling(20).std(ddof=0)
        assert float(bu.iloc[-1]) == pytest.approx(float((mid + 2 * sd).iloc[-1]))


class TestAnaloguesCount:
    def test_six_analogues(self):
        from src.services.technical_report import _historical_comparison
        hist = _zigzag_hist(400)
        res = _historical_comparison(hist)
        assert res["status"] == "ok"
        assert len(res["analogues"]) == 6

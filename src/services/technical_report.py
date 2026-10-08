"""Professional end-to-end technical analysis report — data layer + assembler.

Builds the 61-section report document. Deterministic rules only: every
number traces to a fetched series, and a section the data can't support
returns an explicit ``unsupported`` marker instead of invented prose.
"""

import asyncio
import io
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.tools.nse.patterns import analyze as analyze_patterns
from src.tools.nse.technicals import (
    _TIMEFRAMES,
    _acc_dist,
    _adx,
    _atr,
    _bbands,
    _cci,
    _ema,
    _fib_levels,
    _ichimoku,
    _macd,
    _resample_ohlcv,
    _rsi,
    _sma,
    _stoch,
    _williams_r,
    fetch_earnings_dates,
    fetch_history,
)

# Yahoo benchmark symbols per source. SEC defaults to S&P 500, NSE to Nifty 50.
BENCHMARKS = {
    "NSE": "^NSEI",
    "SEC": "^GSPC",
}

_MAX_SECTION = 61  # the report layout this module serves

# Bar-count lookback per change window, per timeframe: the resampled frames
# have different bar lengths, so "1m" is 21 daily bars but 4 weekly bars.
# (Weekly/monthly have no meaningful "1d" window, so it is not offered.)
_CHANGE_WINDOWS = {
    "daily": (("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63), ("1y", 252)),
    "weekly": (("1w", 1), ("1m", 4), ("3m", 13), ("1y", 52)),
    "monthly": (("1m", 1), ("3m", 3), ("1y", 12)),
}


def _round(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def _pct(v, nd: int = 2):
    return _round(v * 100, nd)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unsupported(reason: str) -> Dict[str, Any]:
    return {"status": "unsupported", "reason": reason}


def _ok(payload: Any) -> Dict[str, Any]:
    return {"status": "ok", "data": payload}


# --------------------------------------------------------------------------
# Relative strength
# --------------------------------------------------------------------------

def _rs_vs_benchmark(hist: pd.DataFrame, bench_hist: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Relative-strength line (stock / benchmark) and its trend over the
    overlapping window. A falling ratio with a rising stock still means
    underperformance — that's the point of the ratio."""
    if hist.empty or bench_hist.empty:
        return None
    s = hist["Close"].dropna()
    b = bench_hist["Close"].dropna()
    common = s.index.intersection(b.index)
    if len(common) < 60:
        return None
    s2, b2 = s.loc[common], b.loc[common]
    ratio = s2 / b2
    r = _round(float(ratio.iloc[-1]), 6)
    r_1m = _round(float(ratio.iloc[-21]), 6) if len(ratio) > 21 else None
    r_3m = _round(float(ratio.iloc[-63]), 6) if len(ratio) > 63 else None
    perf_stock = _pct(float(s2.iloc[-1] / s2.iloc[-63] - 1)) if len(s2) > 63 else None
    perf_bench = _pct(float(b2.iloc[-1] / b2.iloc[-63] - 1)) if len(b2) > 63 else None
    verdict = None
    if r_3m is not None:
        if r > r_1m > r_3m:
            verdict = "outperforming (RS rising across 1m and 3m)"
        elif r < r_1m < r_3m:
            verdict = "underperforming (RS falling across 1m and 3m)"
        else:
            verdict = "mixed"
    return {
        "rs_ratio": r,
        "rs_ratio_1m_ago": r_1m,
        "rs_ratio_3m_ago": r_3m,
        "stock_3m_change_pct": perf_stock,
        "benchmark_3m_change_pct": perf_bench,
        "verdict": verdict,
    }


def _benchmark_history(benchmark_symbol: str) -> pd.DataFrame:
    """Benchmark index via yfinance, tolerant of the same rate limits."""
    try:
        import yfinance as yf

        hist = yf.Ticker(benchmark_symbol).history(period="1y")
        if hist is not None and not hist.empty:
            return hist.dropna(subset=["Open", "High", "Low", "Close"], how="any")
    except Exception as exc:  # noqa: BLE001 - RS is additive, never fatal
        logger.warning(f"benchmark history failed for {benchmark_symbol}: {exc!r}")
    return pd.DataFrame()


# --------------------------------------------------------------------------
# VWAP
# --------------------------------------------------------------------------

def _anchored_vwap(hist: pd.DataFrame) -> Optional[float]:
    """VWAP anchored to the first bar of the given window (daily bars). For
    intraday sessions the anchor resets each session — handled by the
    intraday fetch path. The caller attaches the anchor window as metadata."""
    if hist.empty or "Volume" not in hist.columns:
        return None
    tp = (hist["High"] + hist["Low"] + hist["Close"]) / 3.0
    vol = hist["Volume"].astype(float)
    total_vol = vol.sum()
    if total_vol <= 0:
        return None
    return _round(float((tp * vol).sum() / total_vol))


# --------------------------------------------------------------------------
# Multi-timeframe collection
# --------------------------------------------------------------------------

def _collect_timeframe(
    timeframe: str,
    hist_full: pd.DataFrame,
    resample_rule: Optional[str],
) -> Dict[str, Any]:
    """Indicators + structure for one timeframe, from one shared 5y frame.
    Never raises."""
    try:
        if resample_rule is not None:
            hist = _resample_ohlcv(hist_full, resample_rule)
            if timeframe == "weekly":
                hist = hist.tail(280)  # sma_200 weekly needs ~4.5y of weeks
        else:
            hist = hist_full.tail(320)  # daily: sma_200 needs 200 bars
        if hist.empty:
            return {"timeframe": timeframe, "error": "no price data"}

        close, high, low = hist["Close"], hist["High"], hist["Low"]
        volume = hist.get("Volume")

        out: Dict[str, Any] = {
            "timeframe": timeframe,
            "bars": int(len(close)),
            "current_price": _round(float(close.iloc[-1])),
        }

        def add(key: str, series, nd: int = 4):
            try:
                v = series.iloc[-1]
                val = _round(v, nd)
                if val is not None:
                    out[key] = val
            except (IndexError, TypeError):
                pass

        for p in (20, 50, 200):
            if len(close) >= p:
                s = _sma(close, p)
                add(f"sma_{p}", s)
                # slope over the last 5 bars: position alone hides a flat/falling MA
                if len(close) >= p + 5 and float(s.iloc[-6]) == s.iloc[-6]:
                    prev = float(s.iloc[-6])
                    if prev:
                        out[f"sma_{p}_slope_pct"] = _pct(float(s.iloc[-1]) / prev - 1)
        # golden/death cross state at last bar (cheap, needs only SMA series)
        if len(close) >= 200:
            out["golden_death_cross_state"] = (
                "golden-cross regime" if float(_sma(close, 50).iloc[-1]) > float(_sma(close, 200).iloc[-1])
                else "death-cross regime"
            )
        for p in (12, 26):
            if len(close) >= p:
                add(f"ema_{p}", _ema(close, p))
        if len(close) >= 14:
            add("rsi_14", _rsi(close, 14))
            add("atr_14", _atr(high, low, close, 14))
            adx, pdi, mdi = _adx(high, low, close, 14)
            add("adx_14", adx)
            add("plus_di_14", pdi)
            add("minus_di_14", mdi)
            add("williams_r_14", _williams_r(high, low, close, 14))
            k, d = _stoch(high, low, close, 14, 3, 3)
            add("stoch_k", k)
            add("stoch_d", d)
        if len(close) >= 26:
            macd, sig, histg = _macd(close, 12, 26, 9)
            add("macd", macd)
            add("macd_signal", sig)
            add("macd_hist", histg)
        if len(close) >= 20:
            bu, bm, bl = _bbands(close, 20, 2.0)
            add("bb_upper", bu)
            add("bb_middle", bm)
            add("bb_lower", bl)
            add("cci_20", _cci(high, low, close, 20))
        if volume is not None and not volume.empty:
            out["obv"] = _round(_obv_last(close, volume), 2)
            add("acc_dist", _acc_dist(high, low, close, volume))

        out["structure"] = analyze_patterns(hist)
        out["fibonacci"] = _fib_levels(high, low, lookback=min(120, len(close)))
        out["vwap_anchored"] = _anchored_vwap(hist)
        if out["vwap_anchored"] is not None:
            out["vwap_anchored_meta"] = {
                "anchor": "first_bar_of_window",
                "window_start": str(hist.index[0])[:10],
                "window_end": str(hist.index[-1])[:10],
            }
        vstats = _volume_stats(hist)
        if vstats:
            out["volume_stats"] = vstats
        # Ichimoku needs 52 bars for Senkou B + 26 shift: guard the tail.
        if len(close) >= 78:
            tenkan, kijun, senkou_a, senkou_b, chikou = _ichimoku(high, low, close)
            out["ichimoku_tenkan"] = _round(float(tenkan.iloc[-1]))
            out["ichimoku_kijun"] = _round(float(kijun.iloc[-1]))
            out["ichimoku_senkou_a"] = _round(float(senkou_a.iloc[-1]))
            out["ichimoku_senkou_b"] = _round(float(senkou_b.iloc[-1]))
            out["ichimoku_chikou"] = _round(float(chikou.iloc[-27])) if len(close) > 27 else None
        out["high_52w"] = _round(high.tail(min(252, len(high))).max())
        out["low_52w"] = _round(low.tail(min(252, len(low))).min())
        windows = _CHANGE_WINDOWS.get(timeframe, _CHANGE_WINDOWS["daily"])
        out["changes_window_bars"] = dict(windows)
        for label, n_bars in windows:
            if len(close) > n_bars:
                out[f"change_pct_{label}"] = _pct(
                    float(close.iloc[-1] / close.iloc[-1 - n_bars] - 1)
                )
        return out
    except Exception as exc:  # noqa: BLE001 - per-timeframe isolation
        logger.warning(f"timeframe {timeframe} failed: {exc!r}")
        return {"timeframe": timeframe, "error": str(exc)}


def _obv_last(close: pd.Series, volume: pd.Series) -> Optional[float]:
    try:
        direction = np.sign(close.diff()).fillna(0)
        return float((direction * volume).cumsum().iloc[-1])
    except Exception:  # noqa: BLE001
        return None


def _volume_stats(hist: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Volume trend + dated spikes — the series, not just OBV totals."""
    if "Volume" not in hist.columns:
        return None
    vol = hist["Volume"].astype(float)
    if vol.empty or float(vol.sum()) <= 0:
        return None
    avg20 = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else float(vol.mean())
    if avg20 <= 0:
        return None
    recent5 = float(vol.iloc[-5:].mean())
    ratio = recent5 / avg20
    window = vol.iloc[-60:] if len(vol) >= 60 else vol
    spikes = [
        {"date": str(ts)[:10], "multiple_of_avg20": _round(float(v) / avg20)}
        for ts, v in window.items()
        if float(v) > 2 * avg20
    ]
    spikes.sort(key=lambda s: s["multiple_of_avg20"], reverse=True)
    # event days: intraday range outliers — a -15% crash day with a 406->326
    # wick sets the 52w low; flag it so the low is known to be an intraday
    # extreme, not a closing level
    ranges = (
        (hist["High"] - hist["Low"]) / hist["Low"].replace(0, np.nan) * 100
    ).dropna()
    med_range = float(ranges.median()) if len(ranges) else 0.0
    unusual: List[Dict[str, Any]] = []
    if med_range > 0:
        for ts, r in ranges.items():
            if r <= 3 * med_range:
                continue
            bar = hist.loc[ts]
            chg = (
                _round(float(bar["Close"] / bar["Open"] - 1) * 100)
                if bar["Open"] else None
            )
            v = float(hist["Volume"].loc[ts]) if "Volume" in hist.columns else None
            unusual.append({
                "date": str(ts)[:10],
                "range_pct": _round(r),
                "close_change_pct": chg,
                "volume_multiple_of_avg20": _round(v / avg20) if v else None,
            })
        unusual.sort(key=lambda u: u["range_pct"], reverse=True)
    return {
        "avg_volume_20bar": _round(avg20),
        "recent_5bar_avg": _round(recent5),
        "recent_vs_avg20_ratio": _round(ratio),
        "trend": "rising" if ratio > 1.2 else ("falling" if ratio < 0.8 else "stable"),
        "spikes_last_60bars": spikes[:5],
        "unusual_days": unusual[:5],
    }


def _ohlcv_rows(hist: pd.DataFrame) -> List[Dict[str, Any]]:
    """Raw bars as JSON rows so the report is auditable (SMA/MA/structure
    can all be recomputed from these)."""
    has_vol = "Volume" in hist.columns
    rows: List[Dict[str, Any]] = []
    for ts, r in hist.iterrows():
        v = r["Volume"] if has_vol else None
        rows.append({
            "date": str(ts)[:10],
            "open": _round(float(r["Open"])),
            "high": _round(float(r["High"])),
            "low": _round(float(r["Low"])),
            "close": _round(float(r["Close"])),
            "volume": int(v) if v is not None and v == v else None,
        })
    return rows


def _intraday_snapshot(symbol: str, exchange: str) -> Dict[str, Any]:
    """Intraday (5m) data: session VWAP, day range, first-hour structure."""
    try:
        hist = fetch_history(symbol, exchange, period="1d", interval="5m")
        if hist.empty:
            return {"status": "unsupported", "reason": "no intraday data from provider"}
        close = hist["Close"]
        volume = hist.get("Volume")
        tp = (hist["High"] + hist["Low"] + hist["Close"]) / 3.0
        session_vwap = None
        if volume is not None and volume.sum() > 0:
            session_vwap = _round(float((tp * volume).sum() / volume.sum()))
        day_open = _round(hist["Open"].iloc[0])
        day_high = _round(hist["High"].max())
        day_low = _round(hist["Low"].min())
        last = _round(close.iloc[-1])
        return {
            "status": "ok",
            "bars": int(len(hist)),
            "last": last,
            "day_open": day_open,
            "day_high": day_high,
            "day_low": day_low,
            "session_vwap": session_vwap,
            "position_in_day_range_pct": _pct(
                (last - day_low) / (day_high - day_low)
            ) if day_high and day_low and day_high > day_low else None,
            "above_vwap": (last > session_vwap) if (last and session_vwap) else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"intraday snapshot failed for {symbol}.{exchange}: {exc!r}")
        return {"status": "unsupported", "reason": str(exc)}


# --------------------------------------------------------------------------
# Levels engine (sections 41-46, 30)
# --------------------------------------------------------------------------

def _build_levels(tfs: Dict[str, Any]) -> Dict[str, Any]:
    daily = tfs.get("daily") or {}
    if not daily or "error" in daily:
        return _unsupported("daily timeframe unavailable")
    sr = (daily.get("structure") or {}).get("support_resistance") or {}
    fib = daily.get("fibonacci") or {}
    atr = daily.get("atr_14")
    price = daily.get("current_price") or _round(daily.get("high_52w"))
    if price is None:
        return _unsupported("no price on daily timeframe")

    supports = sorted(
        (l["price"] for l in sr.get("supports", []) if isinstance(l, dict)),
        reverse=True,
    )
    resistances = sorted(
        l["price"] for l in sr.get("resistances", []) if isinstance(l, dict)
    )
    vwap = daily.get("vwap_anchored")

    entry_zones = [
        {"kind": "support", "level": s} for s in supports[:3]
    ]
    if vwap and vwap < price:
        entry_zones.append({"kind": "vwap", "level": vwap})
    fib_levels = (fib.get("levels") or {})
    for label in ("38.2%", "50.0%", "61.8%"):
        lvl = fib_levels.get(label)
        if lvl is not None and lvl < price:
            entry_zones.append({"kind": f"fib_{label}", "level": lvl})
    entry_zones.sort(key=lambda x: x["level"], reverse=True)

    exit_zones = [{"kind": "resistance", "level": r} for r in resistances[:3]]
    for label in ("23.6%", "38.2%"):
        lvl = fib_levels.get(label)
        if lvl is not None and lvl > price:
            exit_zones.append({"kind": f"fib_{label}", "level": lvl})
    exit_zones.sort(key=lambda x: x["level"])

    stop = None
    stop_basis = None
    swing_low = supports[0] if supports else None
    if swing_low is not None:
        stop = swing_low
        stop_basis = "nearest confirmed support"
    if atr is not None:
        atr_stop = _round(price - 2 * atr)
        if stop is None or atr_stop > stop * 0.98:
            stop = atr_stop
            stop_basis = "2x ATR below price (tighter of structure/ATR)"

    tp1 = resistances[0] if resistances else None
    tp2 = resistances[1] if len(resistances) > 1 else None
    tp3 = resistances[2] if len(resistances) > 2 else None

    rr = None
    if stop is not None and tp1 is not None and price > stop:
        rr = _round((tp1 - price) / (price - stop), 2)

    invalidation = None
    if supports:
        invalidation = supports[-1]  # deepest support cluster breaks the structure
    return {
        "status": "ok",
        "reference_price": price,
        "entry_zones": entry_zones,
        "exit_zones": exit_zones,
        "stop_loss": {"level": stop, "basis": stop_basis},
        "targets": {
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "basis": "clustered resistance levels (1-2-3)",
        },
        "risk_reward_to_tp1": rr,
        "invalidation_level": invalidation,
        "invalidation_basis": "loss of the deepest support cluster invalidates the bullish structure",
        "atr_14": atr,
    }


# --------------------------------------------------------------------------
# Confluence / signals engine (sections 5, 33-36, 49-52)
# --------------------------------------------------------------------------

def _signal(name: str, value: Any, bias: str, note: str = "") -> Dict[str, Any]:
    return {"signal": name, "value": value, "bias": bias, "note": note}


def _daily_signals(daily: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic bullish/bearish calls for the signal summary."""
    out: List[Dict[str, Any]] = []
    price = daily.get("current_price")
    if price is None:
        return out

    for p in (20, 50, 200):
        ma = daily.get(f"sma_{p}")
        if ma is not None:
            out.append(_signal(
                f"price_vs_sma_{p}",
                {"price": price, "sma": ma},
                "bullish" if price > ma else "bearish",
            ))
    sma50, sma200 = daily.get("sma_50"), daily.get("sma_200")
    if sma50 is not None and sma200 is not None:
        out.append(_signal(
            "golden_death_cross_state",
            {"sma_50": sma50, "sma_200": sma200},
            "bullish" if sma50 > sma200 else "bearish",
            note="50 above 200 = golden-cross state; below = death-cross state",
        ))
    rsi = daily.get("rsi_14")
    if rsi is not None:
        if rsi >= 70:
            bias, note = "bearish", "overbought"
        elif rsi <= 30:
            bias, note = "bullish", "oversold"
        elif rsi > 60:
            bias, note = "bullish", ""
        elif rsi < 40:
            bias, note = "bearish", ""
        else:
            bias, note = "neutral", "40-60 neutral band"
        out.append(_signal("rsi_14", rsi, bias, note))
    macd, sig, histg = daily.get("macd"), daily.get("macd_signal"), daily.get("macd_hist")
    if macd is not None and sig is not None:
        out.append(_signal(
            "macd_vs_signal", {"macd": macd, "signal": sig},
            "bullish" if macd > sig else "bearish",
        ))
    stoch_k, stoch_d = daily.get("stoch_k"), daily.get("stoch_d")
    if stoch_k is not None and stoch_d is not None:
        bias = "bullish" if stoch_k > stoch_d else "bearish"
        note = ""
        if stoch_k >= 80:
            note = "overbought"
        elif stoch_k <= 20:
            note = "oversold"
        out.append(_signal("stochastic", {"k": stoch_k, "d": stoch_d}, bias, note))
    adx = daily.get("adx_14")
    if adx is not None:
        pdi, mdi = daily.get("plus_di_14"), daily.get("minus_di_14")
        if adx <= 20:
            bias, note = "neutral", "weak trend — direction unreliable"
        else:
            # ADX measures strength only; direction comes from +DI vs -DI
            bias = "bullish" if (pdi or 0) > (mdi or 0) else "bearish"
            note = "strength from ADX; direction from +DI vs -DI"
        out.append(_signal(
            "adx_14", {"adx": adx, "plus_di": pdi, "minus_di": mdi}, bias, note,
        ))
    cci = daily.get("cci_20")
    if cci is not None:
        if cci > 50:
            bias = "bullish"
        elif cci < -50:
            bias = "bearish"
        else:
            bias = "neutral"
        out.append(_signal(
            "cci_20", cci, bias,
            "±50 neutral band" if bias == "neutral" else "",
        ))
    wpr = daily.get("williams_r_14")
    if wpr is not None:
        out.append(_signal(
            "williams_r_14", wpr,
            "bullish" if wpr > -50 else "bearish",
            note="overbought" if wpr > -20 else ("oversold" if wpr < -80 else ""),
        ))
    vwap = daily.get("vwap_anchored")
    if vwap is not None:
        out.append(_signal(
            "price_vs_vwap", {"price": price, "vwap": vwap},
            "bullish" if price > vwap else "bearish",
            note="anchored to window start (see vwap_anchored_meta), not a session VWAP",
        ))
    return out


def _confluence(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    bull = sum(1 for s in signals if s["bias"] == "bullish")
    bear = sum(1 for s in signals if s["bias"] == "bearish")
    neutral = sum(1 for s in signals if s["bias"] == "neutral")
    total = bull + bear + neutral
    if total == 0:
        overall = "neutral"
    elif bull / total >= 0.6 and bull > bear:
        overall = "bullish"
    elif bear / total >= 0.6 and bear > bull:
        overall = "bearish"
    else:
        overall = "mixed"
    return {
        "overall": overall,
        "bullish": bull,
        "bearish": bear,
        "neutral": neutral,
        "total_signals": total,
        "bullish_share": _pct(bull / total) if total else None,
    }


def _mtf_matrix(tfs: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for tf in ("intraday", "daily", "weekly", "monthly"):
        d = tfs.get(tf) or {}
        if not d or "error" in d:
            rows.append({"timeframe": tf, "state": "unavailable"})
            continue
        if tf == "intraday":
            # the intraday snapshot has its own shape (last/vwap, no SMAs)
            if d.get("status") == "ok" and d.get("last") is not None:
                state = None
                if d.get("above_vwap") is not None:
                    state = "bullish" if d["above_vwap"] else "bearish"
                rows.append({
                    "timeframe": tf,
                    "state": state or "neutral",
                    "basis": "price vs session VWAP",
                })
            else:
                rows.append({"timeframe": tf, "state": "unavailable"})
            continue
        price = d.get("current_price")
        if price is None:
            rows.append({"timeframe": tf, "state": "unavailable"})
            continue
        bull, bear = 0, 0
        for p in (20, 50):
            ma = d.get(f"sma_{p}")
            if ma is not None:
                if price > ma:
                    bull += 1
                else:
                    bear += 1
        rsi = d.get("rsi_14")
        if rsi is not None:
            if rsi > 60:
                bull += 1
            elif rsi < 40:
                bear += 1
        macd, sig = d.get("macd"), d.get("macd_signal")
        if macd is not None and sig is not None:
            if macd > sig:
                bull += 1
            else:
                bear += 1
        state = "bullish" if bull > bear else ("bearish" if bear > bull else "neutral")
        rows.append({"timeframe": tf, "state": state, "bull": bull, "bear": bear})
    return rows


def _scenarios(
    tfs: Dict[str, Any],
    levels: Dict[str, Any],
    confluence: Dict[str, Any],
    signals: List[Dict[str, Any]],
) -> Dict[str, Any]:
    daily = tfs.get("daily") or {}
    if (
        not daily
        or "error" in daily
        or daily.get("current_price") is None
        or levels.get("status") != "ok"
    ):
        return _unsupported("daily data or levels unavailable")
    resistances = [z["level"] for z in levels.get("exit_zones", []) if z["kind"] == "resistance"]
    supports = [z["level"] for z in levels.get("entry_zones", []) if z["kind"] == "support"]
    res1 = resistances[0] if resistances else None
    sup1 = supports[0] if supports else None
    overall = confluence.get("overall", "neutral")
    bull_factors = [s["signal"] for s in signals if s["bias"] == "bullish"]
    bear_factors = [s["signal"] for s in signals if s["bias"] == "bearish"]
    targets = levels.get("targets") or {}
    invalidation = levels.get("invalidation_level")

    bull_share = confluence.get("bullish_share")
    bear_share = _round(
        100 - (bull_share or 50) - (confluence.get("neutral") /
        max(confluence.get("total_signals") or 1, 1) * 100), 2
    ) if bull_share is not None else None

    return {
        "status": "ok",
        "bullish": {
            "trigger": f"close above resistance {res1}" if res1 else "sustained closes above current range high",
            "targets": [t for t in (targets.get("tp1"), targets.get("tp2"), targets.get("tp3")) if t is not None],
            "invalidation": invalidation,
            "supporting_factors": bull_factors[:4],
            "weighting_hint": f"bullish signals {confluence.get('bullish')} of {confluence.get('total_signals')}",
        },
        "bearish": {
            "trigger": f"close below support {sup1}" if sup1 else "loss of the current range low",
            "targets": supports[:3],
            "invalidation": res1,
            "supporting_factors": bear_factors[:4],
            "weighting_hint": f"bearish signals {confluence.get('bearish')} of {confluence.get('total_signals')}",
        },
        "neutral": {
            "trigger": "hold inside support/resistance band",
            "band": {"support": sup1, "resistance": res1},
            "targets": [sup1, res1],
            "invalidation": "break of either band edge",
            "supporting_factors": [s["signal"] for s in signals if s["bias"] == "neutral"][:4],
            "weighting_hint": overall,
        },
        "bias_from_confluence": overall,
        "signal_shares_pct": {"bullish": bull_share, "bearish": bear_share},
        "note": (
            "deterministic triggers/targets only; the only weighting in this document "
            "is signal_shares_pct (derived from indicator_confluence). Author scenario "
            "probabilities downstream from price_history + levels."
        ),
    }


# --------------------------------------------------------------------------
# Section 57: historical pattern comparison
# --------------------------------------------------------------------------

def _historical_comparison(hist: pd.DataFrame) -> Dict[str, Any]:
    """Compare the recent consolidation/breakout shape with prior analogues
    in the same series. Returns the closest analogues by return-path shape."""
    if hist is None or len(hist) < 120:
        return _unsupported("needs >= 120 daily bars")
    close = hist["Close"].reset_index(drop=True)
    window = 20
    recent = close.iloc[-window:].reset_index(drop=True)
    recent_norm = (recent / recent.iloc[0] - 1).to_numpy()
    best: List[Dict[str, Any]] = []
    n = len(close)
    for start in range(0, n - window - 21):
        seg = close.iloc[start : start + window].reset_index(drop=True)
        seg_norm = (seg / seg.iloc[0] - 1).to_numpy()
        if np.isnan(seg_norm).any():
            continue
        dist = float(np.sqrt(((seg_norm - recent_norm) ** 2).sum()))
        fwd = float(close.iloc[start + window + 20] / close.iloc[start + window] - 1)
        best.append({"start_idx": start, "distance": dist, "forward_20d_pct": fwd * 100})
    best.sort(key=lambda x: x["distance"])
    # overlapping windows are the same episode — two adjacent starts count once
    top: List[Dict[str, Any]] = []
    for cand in best:
        if any(abs(cand["start_idx"] - t["start_idx"]) < window for t in top):
            continue
        top.append(cand)
        if len(top) == 6:
            break
    idx = hist.index
    for t in top:
        t["start_date"] = str(idx[t["start_idx"]])[:10]
        t["end_date"] = str(idx[min(t["start_idx"] + window - 1, len(idx) - 1)])[:10]
        t["forward_20d_pct"] = _round(t["forward_20d_pct"])
        t["distance"] = _round(t["distance"], 5)
    return {
        "status": "ok" if top else "unsupported",
        "note": "shape-match over normalised 20-bar paths; forward returns are what followed those analogues, not a forecast",
        "analogues": top,
    }


# --------------------------------------------------------------------------
# Section 56: catalysts
# --------------------------------------------------------------------------

def _catalysts(
    announcements: Optional[List[Dict[str, Any]]],
    earnings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = [
        {"date": e.get("date"), "title": e.get("title"), "kind": "earnings"}
        for e in (earnings or [])
    ]
    for a in (announcements or [])[:8]:
        title = a.get("heading") or a.get("subject") or a.get("title")
        if title:
            items.append({
                "date": a.get("date") or a.get("broadcast_date"),
                "title": title,
                "kind": "announcement",
            })
    if not items:
        return _unsupported("no announcements or earnings dates available")
    return {
        "status": "ok",
        "note": "upcoming earnings (Yahoo calendar, best-effort) and recent corporate announcements that may act as technical catalysts",
        "items": items,
    }


# --------------------------------------------------------------------------
# Section 58: technical risk flags
# --------------------------------------------------------------------------

def _risk_factors(
    daily: Dict[str, Any],
    structure: Dict[str, Any],
    confluence: Dict[str, Any],
    hv: Optional[Dict[str, Any]],
    price: Optional[float],
    bench_ok: bool,
) -> List[str]:
    """Deterministic risk flags. A -39% year with a death cross must never
    land on 'no quantitative risk flags triggered'."""
    flags: List[str] = []
    adx = daily.get("adx_14")
    if adx is not None and adx < 20:
        flags.append("ADX below 20: range regime — trend-following signals less reliable")
    if hv and hv.get("hv_20d_annualized_pct") and hv["hv_20d_annualized_pct"] > 60:
        flags.append(f"20d realized volatility {hv['hv_20d_annualized_pct']}%: elevated — size positions accordingly")
    if price is not None and daily.get("high_52w"):
        dd = (price / daily["high_52w"] - 1) * 100
        if dd < -20:
            flags.append(f"{_round(dd, 1)}% below the 52-week high: deep drawdown")
    if daily.get("golden_death_cross_state") == "death-cross regime":
        flags.append("SMA50 below SMA200: death-cross regime")
    if price is not None and daily.get("sma_200") and price < daily["sma_200"]:
        flags.append("price below SMA200: long-term trend filter is bearish")
    chg_1y = daily.get("change_pct_1y")
    if chg_1y is not None and chg_1y < -20:
        flags.append(f"1y change {chg_1y}%: significant capital loss over the last year")
    cls = (structure.get("market_structure") or {}).get("classification") or ""
    if cls.startswith("uptrend") and confluence.get("overall") in ("bearish", "mixed"):
        flags.append(
            "pivot structure says uptrend while indicator confluence is "
            f"{confluence.get('overall')} — treat the structure label as low confidence"
        )
    div = structure.get("divergence", {}).get("divergences") or []
    for d in div:
        flags.append(f"{d['type']} divergence against current trend at {d['anchor']}")
    if not bench_ok:
        flags.append("relative strength could not be computed — benchmark feed unavailable")
    return flags


_DASHBOARD_KEYS = (
    "sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "stoch_k", "stoch_d", "adx_14", "plus_di_14", "minus_di_14",
    "cci_20", "williams_r_14", "atr_14",
    "bb_upper", "bb_middle", "bb_lower", "golden_death_cross_state",
)

# --------------------------------------------------------------------------
# Main assembler
# --------------------------------------------------------------------------

REPORT_SECTIONS = [
    "executive_summary", "asset_overview", "current_price_market_data",
    "multi_timeframe_price_analysis", "price_history", "trend_analysis",
    "market_structure",
    "support_resistance", "supply_demand_zones", "price_action_analysis",
    "candlestick_analysis", "chart_pattern_analysis", "breakout_breakdown",
    "moving_averages", "momentum_analysis", "rsi_analysis", "macd_analysis",
    "stochastic_analysis", "adx_analysis", "cci_analysis", "williams_r_analysis",
    "volume_analysis", "volume_profile", "vwap_analysis",
    "obv_accumulation_distribution", "volatility_analysis", "atr_analysis",
    "bollinger_bands_analysis", "historical_volatility", "relative_strength",
    "fibonacci_analysis", "ichimoku_analysis", "gap_analysis",
    "market_regime", "trend_vs_range", "bullish_bearish_signals",
    "indicator_confluence", "divergence_analysis", "short_term_setup",
    "medium_term_setup", "long_term_structure", "entry_zones", "exit_zones",
    "stop_loss", "take_profit_targets", "risk_reward", "invalidation_levels",
    "breakout_scenarios", "breakdown_scenarios", "bullish_scenario",
    "bearish_scenario", "neutral_scenario", "technical_signal_summary",
    "indicator_dashboard", "mtf_signal_matrix", "key_technical_levels",
    "upcoming_catalysts", "historical_pattern_comparison",
    "technical_risk_factors", "overall_assessment", "data_sources_methodology",
]
assert len(REPORT_SECTIONS) == _MAX_SECTION


async def build_technical_report(
    symbol: str,
    source: str = "NSE",
    timeframes: str = "daily,weekly,monthly",
    announcements: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble the full 61-section report. Per-section failure degrades to
    an `unsupported` marker; the document itself always returns 200."""
    symbol = symbol.upper()
    source_u = source.upper()
    requested = [t.strip() for t in timeframes.split(",") if t.strip()]

    exchange = source_u if source_u != "SEC" else "NASDAQ"
    tfs: Dict[str, Any] = {}

    def run_collection():
        # One 5y fetch serves every timeframe (sliced/resampled inside
        # _collect_timeframe): 3x fewer Yahoo calls means 3x less exposure
        # to the datacenter-IP rate limiting that blanks cold instances.
        hist_5y = fetch_history(symbol, exchange, period="5y")
        for tf in requested:
            if tf == "intraday":
                tfs["intraday"] = _intraday_snapshot(symbol, exchange)
                continue
            if tf not in _TIMEFRAMES:
                tfs[tf] = {"error": f"unknown timeframe '{tf}'"}
                continue
            rule, _period = _TIMEFRAMES[tf]
            tfs[tf] = _collect_timeframe(tf, hist_5y, rule)
        return tfs, hist_5y

    tfs, hist_5y = await asyncio.to_thread(run_collection)

    # 1y daily frame, fetched once: relative strength, historical vol,
    # historical pattern comparison and the price-action candle all use it.
    hist_1y = await asyncio.to_thread(fetch_history, symbol, exchange, "1y")

    # Best-effort earnings calendar for section 56 (never fatal).
    earnings = await asyncio.to_thread(fetch_earnings_dates, symbol, exchange)

    # Benchmark history once for relative strength on the daily frame.
    daily = tfs.get("daily") or {}
    bench_ok = False
    if not daily.get("error"):
        bench_sym = BENCHMARKS.get(source_u, "^NSEI")
        bench_hist = await asyncio.to_thread(_benchmark_history, bench_sym)
        rs = _rs_vs_benchmark(hist_1y, bench_hist) if not bench_hist.empty else None
        if rs:
            tfs["relative_strength_vs_benchmark"] = rs
            bench_ok = True

    sections: Dict[str, Any] = {}

    # ---- price & overview (sections 2-4) ----
    price = daily.get("current_price")
    intraday = tfs.get("intraday") or {}
    sections["price_action_analysis"] = (
        _ok({
            "day_open": intraday.get("day_open"),
            "day_high": intraday.get("day_high"),
            "day_low": intraday.get("day_low"),
            "last": intraday.get("last"),
            "position_in_day_range_pct": intraday.get("position_in_day_range_pct"),
            "above_vwap": intraday.get("above_vwap"),
            "daily_candle": (
                # last completed daily candle shape
                {
                    "open": _round(float(hist_1y["Open"].iloc[-1])),
                    "close": _round(float(hist_1y["Close"].iloc[-1])),
                    "direction": "up" if hist_1y["Close"].iloc[-1] >= hist_1y["Open"].iloc[-1] else "down",
                }
                if not hist_1y.empty else None
            ),
        })
        if intraday.get("status") == "ok" else
        _ok({"note": "intraday data unavailable; price action described from the last completed daily candle", **({})})
        if not hist_1y.empty else
        _unsupported("no price data")
    )
    sections["asset_overview"] = _ok({"symbol": symbol, "source": source_u})
    sections["current_price_market_data"] = _ok({
        "current_price": price,
        "high_52w": daily.get("high_52w"),
        "low_52w": daily.get("low_52w"),
        "changes_pct": {k: daily.get(k) for k in ("change_pct_1d", "change_pct_1w", "change_pct_1m", "change_pct_3m", "change_pct_1y")},
    }) if price is not None else _unsupported("no live price")
    mtf_price = {}
    for tf in requested:
        d = tfs.get(tf) or {}
        if d.get("error"):
            mtf_price[tf] = {"status": "unsupported", "reason": d["error"]}
        else:
            mtf_price[tf] = _ok({
                "bars": d.get("bars"),
                "last": d.get("current_price"),
                "changes_pct": {k: d.get(k) for k in ("change_pct_1d", "change_pct_1w", "change_pct_1m", "change_pct_3m", "change_pct_1y") if k in d},
                "changes_window_bars": d.get("changes_window_bars"),
            })
    sections["multi_timeframe_price_analysis"] = _ok(mtf_price)

    # ---- raw OHLCV (5): auditable bars, weekly covers the 200-week MA ----
    if hist_5y is None or hist_5y.empty:
        sections["price_history"] = _unsupported("no price data")
    else:
        # same frames the indicators used, so every derived number is
        # recomputable from this section alone
        weekly_hist = _resample_ohlcv(hist_5y, "W").tail(280)
        weekly_rows = _ohlcv_rows(weekly_hist)
        if weekly_rows:
            # last weekly bar is partial when the week isn't over yet
            if hist_5y.index[-1] < weekly_hist.index[-1]:
                weekly_rows[-1]["is_partial"] = True
            # volume before ~2y isn't comparable (provider adjustments) —
            # null it so consumer medians aren't skewed by ancient prints
            cutoff = hist_5y.index[-1] - pd.Timedelta(days=730)
            for row, ts in zip(weekly_rows, weekly_hist.index):
                if ts < cutoff:
                    row["volume"] = None
        sections["price_history"] = _ok({
            "adjustment": "split+dividend adjusted",
            "daily": _ohlcv_rows(hist_5y.tail(320)),
            "weekly": weekly_rows,
            "note": "weekly resampled from daily (W rule); 280 weekly bars ≈ 5.4y, enough for the 200-week MA; weekly volume nulled before ~2y (not comparable)",
        })

    # ---- indicators (sections 13-28) ----
    ind_keys = [
        ("moving_averages", ["sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "golden_death_cross_state"]),
        ("momentum_analysis", ["rsi_14", "macd", "macd_signal", "macd_hist", "stoch_k", "stoch_d", "cci_20", "williams_r_14"]),
        ("rsi_analysis", ["rsi_14"]),
        ("macd_analysis", ["macd", "macd_signal", "macd_hist"]),
        ("stochastic_analysis", ["stoch_k", "stoch_d"]),
        ("adx_analysis", ["adx_14", "plus_di_14", "minus_di_14"]),
        ("cci_analysis", ["cci_20"]),
        ("williams_r_analysis", ["williams_r_14"]),
        ("atr_analysis", ["atr_14"]),
        ("bollinger_bands_analysis", ["bb_upper", "bb_middle", "bb_lower"]),
    ]
    for section, keys in ind_keys:
        payload = {k: daily.get(k) for k in keys if daily.get(k) is not None}
        sections[section] = _ok(payload) if payload else _unsupported("not computable on available data")

    # ---- volume (21, 23, 24) ----
    weekly = tfs.get("weekly") or {}
    monthly = tfs.get("monthly") or {}
    vol_payload = {k: daily.get(k) for k in ("obv", "acc_dist") if daily.get(k) is not None}
    if daily.get("volume_stats"):
        vol_payload["volume_stats"] = daily["volume_stats"]
    if weekly.get("volume_stats"):
        vol_payload["weekly_volume_stats"] = weekly["volume_stats"]
    sections["volume_analysis"] = _ok(vol_payload) if vol_payload else _unsupported("no volume data")
    sections["obv_accumulation_distribution"] = _ok(vol_payload) if vol_payload else _unsupported("no volume data")
    sections["volume_profile"] = (
        _ok(daily["structure"]["volume_profile"])
        if daily.get("structure", {}).get("volume_profile")
        else _unsupported("needs >= 20 bars with volume")
    )
    vwap_data = {
        "daily_anchored_vwap": daily.get("vwap_anchored"),
        "daily_anchored_vwap_meta": daily.get("vwap_anchored_meta"),
    }
    if "intraday" in tfs and tfs["intraday"].get("status") == "ok":
        vwap_data["intraday_session_vwap"] = tfs["intraday"].get("session_vwap")
    sections["vwap_analysis"] = _ok(vwap_data) if vwap_data["daily_anchored_vwap"] or vwap_data.get("intraday_session_vwap") else _unsupported("no volume data")

    # ---- volatility (25, 28) ----
    hv = None
    if not hist_1y.empty and len(hist_1y) > 60:
        ret = np.log(hist_1y["Close"] / hist_1y["Close"].shift(1)).dropna()
        hv20 = _round(float(ret.tail(20).std(ddof=1) * math.sqrt(252) * 100))
        hv60 = _round(float(ret.tail(60).std(ddof=1) * math.sqrt(252) * 100))
        hv = {"hv_20d_annualized_pct": hv20, "hv_60d_annualized_pct": hv60}
    sections["volatility_analysis"] = _ok({
        "atr_14": daily.get("atr_14"),
        **(hv or {}),
    }) if (daily.get("atr_14") or hv) else _unsupported("insufficient history")
    sections["historical_volatility"] = _ok(hv) if hv else _unsupported("insufficient history")

    # ---- relative strength (29) ----
    sections["relative_strength"] = (
        _ok(tfs["relative_strength_vs_benchmark"]) if bench_ok
        else _unsupported(f"benchmark history unavailable for {BENCHMARKS.get(source_u)}")
    )

    # ---- structure sections (6-12, 22, 32, 37) ----
    structure = daily.get("structure") or {}
    wstructure = (weekly.get("structure") or {}) if weekly else {}
    sections["market_structure"] = _ok(structure["market_structure"]) if structure.get("market_structure") else _unsupported("insufficient bars")
    if structure.get("support_resistance"):
        sr_payload = dict(structure["support_resistance"])
        if wstructure.get("support_resistance"):
            sr_payload["weekly"] = wstructure["support_resistance"]
        sections["support_resistance"] = _ok(sr_payload)
    else:
        sections["support_resistance"] = _unsupported("insufficient bars")
    sections["supply_demand_zones"] = _ok(structure["supply_demand_zones"]) if structure.get("supply_demand_zones") else _unsupported("no zones detected")
    sections["candlestick_analysis"] = _ok({
        "daily": structure.get("candlesticks"),
        "weekly": wstructure.get("candlesticks"),
    }) if structure.get("candlesticks") else _unsupported("insufficient bars")
    sections["chart_pattern_analysis"] = _ok({
        "daily": structure.get("chart_patterns"),
        "weekly": wstructure.get("chart_patterns"),
    }) if structure.get("chart_patterns") else _unsupported("insufficient bars")
    sections["breakout_breakdown"] = _ok(structure["chart_patterns"]["breakout"]) if structure.get("chart_patterns", {}).get("breakout") else _ok({"breakout": None, "note": "no active breakout/breakdown from the 60-bar range"})
    sections["gap_analysis"] = _ok(structure["gaps"]) if structure.get("gaps") else _unsupported("no gaps found")
    sections["divergence_analysis"] = _ok(structure["divergence"]) if structure.get("divergence") else _unsupported("insufficient bars")

    # ---- Fibonacci & Ichimoku (30, 31) ----
    fib = daily.get("fibonacci") or {}
    sections["fibonacci_analysis"] = _ok(fib) if fib else _unsupported("insufficient bars for swing levels")
    ich = {k: daily.get(k) for k in (
        "ichimoku_tenkan", "ichimoku_kijun", "ichimoku_senkou_a",
        "ichimoku_senkou_b", "ichimoku_chikou",
    ) if daily.get(k) is not None}
    if ich:
        price = daily.get("current_price")
        sa, sb = daily.get("ichimoku_senkou_a"), daily.get("ichimoku_senkou_b")
        if price is not None and sa is not None and sb is not None:
            ich["price_vs_cloud"] = (
                "above cloud (bullish)" if price > max(sa, sb)
                else "below cloud (bearish)" if price < min(sa, sb)
                else "inside cloud (neutral)"
            )
        sections["ichimoku_analysis"] = _ok(ich)
    else:
        sections["ichimoku_analysis"] = _unsupported("needs >= 78 bars (Senkou B shift)")

    # ---- signals & confluence (35-36, 49) — computed before the trend
    # sections so trend_analysis can state structure vs momentum apart ----
    signals = _daily_signals(daily)
    confluence = _confluence(signals)

    # ---- trend & regime (5, 33, 34) ----
    ms = structure.get("market_structure") or {}
    price_vs_sma200 = None
    if price is not None and daily.get("sma_200"):
        price_vs_sma200 = _pct(price / daily["sma_200"] - 1)
    structural_dir = ms.get("classification") or ""
    structural_dir = (
        "bullish" if structural_dir.startswith("uptrend")
        else "bearish" if structural_dir.startswith("downtrend")
        else structural_dir or "unknown"
    )
    adx_val = daily.get("adx_14")
    di_dir = (
        "unreliable (ADX<=20)"
        if not adx_val or adx_val <= 20
        else "bullish" if (daily.get("plus_di_14") or 0) > (daily.get("minus_di_14") or 0)
        else "bearish"
    )
    trend_dimensions = {
        "structural": {
            "direction": structural_dir,
            "confidence": ms.get("confidence"),
            "basis": "last confirmed pivot pairs (HH/HL vs LH/LL)",
        },
        "current": {
            "momentum": confluence["overall"],
            "regime": (structure.get("trend_regime") or {}).get("regime"),
            "di_direction": di_dir,
            "price_vs_sma200_pct": price_vs_sma200,
        },
        "agreement": (
            structural_dir == confluence["overall"]
            if structural_dir in ("bullish", "bearish")
            and confluence["overall"] in ("bullish", "bearish")
            else None
        ),
    }
    sections["trend_analysis"] = _ok({
        "market_structure": ms.get("classification"),
        "structure_confidence": ms.get("confidence"),
        "price_vs_sma200_pct": price_vs_sma200,
        "trend_regime": structure.get("trend_regime"),
        # pivot label = long-term structure; confluence = current momentum.
        # They can legitimately disagree — read both, don't merge them.
        "current_momentum": confluence["overall"],
        "trend_dimensions": trend_dimensions,
        "sma_alignment": {
            "sma_20": daily.get("sma_20"),
            "sma_50": daily.get("sma_50"),
            "sma_200": daily.get("sma_200"),
        },
        "sma_slopes_pct": {
            f"sma_{p}": daily.get(f"sma_{p}_slope_pct") for p in (20, 50, 200)
            if daily.get(f"sma_{p}_slope_pct") is not None
        },
    }) if structure else _unsupported("insufficient bars")
    sections["market_regime"] = _ok(structure.get("trend_regime") or {}) if structure.get("trend_regime") else _unsupported("insufficient bars")
    sections["trend_vs_range"] = _ok(structure.get("trend_regime") or {}) if structure.get("trend_regime") else _unsupported("insufficient bars")

    sections["bullish_bearish_signals"] = _ok(signals) if signals else _unsupported("no price data")
    sections["indicator_confluence"] = _ok(confluence)
    sections["technical_signal_summary"] = _ok({
        "overall": confluence["overall"],
        "headline": f"{confluence['bullish']} bullish / {confluence['bearish']} bearish / {confluence['neutral']} neutral of {confluence['total_signals']} deterministic signals",
    })

    # ---- levels & scenarios (38-48, 50-51, 55) ----
    levels = _build_levels(tfs)
    sections["entry_zones"] = _ok(levels["entry_zones"]) if levels.get("status") == "ok" else levels
    sections["exit_zones"] = _ok(levels["exit_zones"]) if levels.get("status") == "ok" else levels
    sections["stop_loss"] = _ok(levels["stop_loss"]) if levels.get("status") == "ok" else levels
    sections["take_profit_targets"] = _ok(levels["targets"]) if levels.get("status") == "ok" else levels
    sections["risk_reward"] = _ok({"risk_reward_to_tp1": levels.get("risk_reward_to_tp1")}) if levels.get("status") == "ok" else levels
    sections["invalidation_levels"] = _ok({
        "level": levels.get("invalidation_level"),
        "basis": levels.get("invalidation_basis"),
    }) if levels.get("status") == "ok" else levels
    sections["key_technical_levels"] = _ok({
        "reference_price": levels.get("reference_price"),
        "entry_zones": levels.get("entry_zones"),
        "exit_zones": levels.get("exit_zones"),
        "stop_loss": levels.get("stop_loss"),
        "targets": levels.get("targets"),
        "invalidation": levels.get("invalidation_level"),
    }) if levels.get("status") == "ok" else levels

    scenarios = _scenarios(tfs, levels, confluence, signals)
    for sec, key in (
        ("bullish_scenario", "bullish"),
        ("bearish_scenario", "bearish"),
        ("neutral_scenario", "neutral"),
    ):
        if scenarios.get("status") == "ok":
            sections[sec] = _ok(scenarios[key])
        else:
            sections[sec] = scenarios
    sections["breakout_scenarios"] = _ok({
        "above": levels.get("exit_zones", [None])[0] if levels.get("exit_zones") else None,
        "volume_confirmed_breakout": structure.get("chart_patterns", {}).get("breakout"),
    }) if levels.get("status") == "ok" else levels
    sections["breakdown_scenarios"] = _ok({
        "below": levels.get("entry_zones", [None])[0] if levels.get("entry_zones") else None,
        "volume_confirmed_breakdown": structure.get("chart_patterns", {}).get("breakout"),
    }) if levels.get("status") == "ok" else levels

    # ---- setups (38-40) ----
    short_tf = tfs.get("intraday") if tfs.get("intraday", {}).get("status") == "ok" else tfs.get("daily")
    sections["short_term_setup"] = _ok({
        "basis_timeframe": "intraday" if short_tf is tfs.get("intraday") else "daily",
        "mtf_state": next((r for r in _mtf_matrix(tfs) if r["timeframe"] == ("intraday" if short_tf is tfs.get("intraday") else "daily")), None),
        "nearest_resistance": (levels.get("exit_zones") or [{}])[0].get("level"),
        "nearest_support": (levels.get("entry_zones") or [{}])[0].get("level"),
    }) if levels.get("status") == "ok" else levels
    wms = (weekly.get("structure") or {}).get("market_structure") or {}
    sections["medium_term_setup"] = _ok({
        "basis_timeframe": "weekly",
        "bars": weekly.get("bars"),
        "rsi_14": weekly.get("rsi_14"),
        "sma_20": weekly.get("sma_20"),
        "sma_50": weekly.get("sma_50"),
        "sma_200": weekly.get("sma_200"),
        "golden_death_cross_state": weekly.get("golden_death_cross_state"),
        "sma_slopes_pct": {
            f"sma_{p}": weekly.get(f"sma_{p}_slope_pct") for p in (20, 50, 200)
            if weekly.get(f"sma_{p}_slope_pct") is not None
        },
        "structure": wms.get("classification"),
        "structure_confidence": wms.get("confidence"),
        "changes_pct": {k: weekly.get(k) for k in ("change_pct_1w", "change_pct_1m", "change_pct_3m", "change_pct_1y") if k in weekly},
        "changes_window_bars": weekly.get("changes_window_bars"),
    }) if weekly and not weekly.get("error") else _unsupported("weekly timeframe unavailable")
    mms = (monthly.get("structure") or {}).get("market_structure") or {}
    sections["long_term_structure"] = _ok({
        "basis_timeframe": "monthly",
        "rsi_14": monthly.get("rsi_14"),
        "sma_20": monthly.get("sma_20"),
        "sma_50": monthly.get("sma_50"),
        "structure": mms.get("classification"),
        "structure_confidence": mms.get("confidence"),
    }) if monthly and not monthly.get("error") else _unsupported("monthly timeframe unavailable")

    # ---- dashboards (53-54) ----
    dashboard = {
        tf: {
            k: tfs[tf].get(k) for k in _DASHBOARD_KEYS
            if tfs[tf].get(k) is not None
        }
        for tf in ("daily", "weekly", "monthly")
        if not (tfs.get(tf) or {}).get("error")
    }
    sections["indicator_dashboard"] = _ok(dashboard) if any(dashboard.values()) else _unsupported("no price data")
    sections["mtf_signal_matrix"] = _ok(_mtf_matrix(tfs))

    # ---- 56, 57 ----
    sections["upcoming_catalysts"] = _catalysts(announcements, earnings)
    sections["historical_pattern_comparison"] = _historical_comparison(hist_1y)

    # ---- 58, 59 ----
    sections["technical_risk_factors"] = _ok(
        _risk_factors(daily, structure, confluence, hv, price, bench_ok)
        or ["no quantitative risk flags triggered"]
    )

    sections["overall_assessment"] = _ok({
        "confluence": confluence["overall"],
        "regime": (structure.get("trend_regime") or {}).get("regime"),
        "structure": (structure.get("market_structure") or {}).get("classification"),
        "structure_confidence": ms.get("confidence"),
        "price_vs_sma200_pct": price_vs_sma200,
        "risk_reward_to_tp1": levels.get("risk_reward_to_tp1") if levels.get("status") == "ok" else None,
        "note": "deterministic synthesis of the sections above; not investment advice",
    })

    # ---- 1 & 61 ----
    # placeholder first: the supported/unsupported counts are filled in after
    # every section (including data_sources_methodology) has been assembled
    wms_dir = (wms.get("classification") or "unknown") if wms else "unknown"
    if trend_dimensions["agreement"] is False:
        trend_headline = (
            f"structural {structural_dir} ({ms.get('confidence')} confidence) conflicts with "
            f"{confluence['overall']} current momentum — weekly structure: {wms_dir}"
        )
    elif trend_dimensions["agreement"] is True:
        trend_headline = f"structural and current trend aligned: {confluence['overall']}"
    else:
        trend_headline = f"current momentum {confluence['overall']}; structure {structural_dir}"
    sections["executive_summary"] = _ok({
        "symbol": symbol,
        "price": price,
        "confluence": confluence["overall"],
        "regime": (structure.get("trend_regime") or {}).get("regime"),
        "structure_class": (structure.get("market_structure") or {}).get("classification"),
        "structure_confidence": ms.get("confidence"),
        "trend_headline": trend_headline,
        "trend_dimensions": trend_dimensions,
        "weekly_structure": wms_dir,
        "sections_supported": None,
        "sections_unsupported": None,
    })
    sections["data_sources_methodology"] = _ok({
        "price_source": "Yahoo Finance (yfinance + chart fallback)",
        "price_adjustment": "split+dividend adjusted (yfinance auto_adjust=True)",
        "bar_filter": "zero-volume placeholder bars (exchange-holiday fills) dropped",
        "filings_source": source_u,
        "indicator_definitions": "pandas-only implementations aligned with pandas_ta / Wilder smoothing (see src/tools/nse/technicals.py)",
        "patterns": "rule-based detectors over confirmed fractal pivots (see src/tools/nse/patterns.py)",
        "levels": "deterministic mapping from clustered S/R + Fibonacci + ATR; no discretion",
        "as_of": _utcnow(),
    })
    sections["executive_summary"]["data"].update({
        "sections_supported": sum(1 for s in sections.values() if isinstance(s, dict) and s.get("status") == "ok"),
        "sections_unsupported": sum(1 for s in sections.values() if isinstance(s, dict) and s.get("status") == "unsupported"),
    })

    ordered = {name: sections.get(name, _unsupported("not assembled")) for name in REPORT_SECTIONS}
    return {
        "symbol": symbol,
        "source": source_u.lower(),
        "as_of": _utcnow(),
        "timeframes_requested": requested,
        "sections_count": len(ordered),
        "sections": ordered,
    }


# --------------------------------------------------------------------------
# Chart image (weekly/daily PNG for chart-image skill inputs)
# --------------------------------------------------------------------------

def render_chart_png(
    symbol: str,
    exchange: str = "NSE",
    timeframe: str = "weekly",
    bars: int = 160,
) -> bytes:
    """Close + SMA20/50/200 + volume PNG. matplotlib Agg, no display —
    the weekly chart image that chart-reading skills take as input."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rule, _period = _TIMEFRAMES.get(timeframe, (None, None))
    hist = fetch_history(symbol, exchange, period="5y")
    if hist is None or hist.empty:
        raise ValueError(f"no price history for {symbol}")
    if rule is not None:
        hist = _resample_ohlcv(hist, rule)
    hist = hist.tail(max(20, min(bars, 400)))
    close = hist["Close"]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    ax1.plot(close.index, close.values, color="black", lw=1.2, label="Close")
    for p, color in ((20, "#e6a817"), (50, "#1f77b4"), (200, "#d62728")):
        if len(close) >= p:
            ma = _sma(close, p)
            ax1.plot(ma.index, ma.values, lw=1.0, color=color, label=f"SMA{p}")
    ax1.set_title(f"{symbol} — {timeframe} (split+dividend adjusted)")
    ax1.legend(loc="best", fontsize=8)
    ax1.grid(alpha=0.3)
    if "Volume" in hist.columns:
        # bar width in days: 0.8 of the median spacing, whatever the timeframe
        step_days = (
            (hist.index[-1] - hist.index[0]).days / max(len(hist) - 1, 1)
            if len(hist) > 1 else 1.0
        )
        ax2.bar(hist.index, hist["Volume"], color="#999999", width=max(step_days * 0.8, 0.1))
        ax2.set_ylabel("Volume")
    ax2.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()

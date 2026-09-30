"""Professional end-to-end technical analysis report — data layer + assembler.

Builds the 60-section report document. Deterministic rules only: every
number traces to a fetched series, and a section the data can't support
returns an explicit ``unsupported`` marker instead of invented prose.
"""

import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.tools.nse.technicals import (
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
    _TIMEFRAMES,
    fetch_history,
)
from src.tools.nse.patterns import analyze as analyze_patterns

# Yahoo benchmark symbols per source. SEC defaults to S&P 500, NSE to Nifty 50.
BENCHMARKS = {
    "NSE": "^NSEI",
    "SEC": "^GSPC",
}

_MAX_SECTION = 60  # the report layout this module serves


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

def _anchored_vwap(hist: pd.DataFrame, anchor: str = "year") -> Optional[float]:
    """VWAP anchored to the period start (daily bars). For intraday sessions
    the anchor resets each session — handled by the intraday fetch path."""
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
    symbol: str,
    exchange: str,
    timeframe: str,
    resample_rule: Optional[str],
    fetch_period: str,
) -> Dict[str, Any]:
    """Indicators + structure for one timeframe. Never raises."""
    try:
        hist = fetch_history(symbol, exchange, period=fetch_period)
        if hist.empty:
            return {"timeframe": timeframe, "error": "no price data"}
        if resample_rule is not None:
            hist = _resample_ohlcv(hist, resample_rule)
            if hist.empty:
                return {"timeframe": timeframe, "error": "resampling produced no rows"}

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
                add(f"sma_{p}", _sma(close, p))
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
        out["change_pct_1d"] = _pct(float(close.iloc[-1] / close.iloc[-2] - 1)) if len(close) > 1 else None
        out["change_pct_1w"] = _pct(float(close.iloc[-1] / close.iloc[-5] - 1)) if len(close) > 5 else None
        out["change_pct_1m"] = _pct(float(close.iloc[-1] / close.iloc[-21] - 1)) if len(close) > 21 else None
        out["change_pct_3m"] = _pct(float(close.iloc[-1] / close.iloc[-63] - 1)) if len(close) > 63 else None
        out["change_pct_1y"] = _pct(float(close.iloc[-1] / close.iloc[-252] - 1)) if len(close) > 252 else None
        return out
    except Exception as exc:  # noqa: BLE001 - per-timeframe isolation
        logger.warning(f"timeframe {timeframe} failed for {symbol}.{exchange}: {exc!r}")
        return {"timeframe": timeframe, "error": str(exc)}


def _obv_last(close: pd.Series, volume: pd.Series) -> Optional[float]:
    try:
        direction = np.sign(close.diff()).fillna(0)
        return float((direction * volume).cumsum().iloc[-1])
    except Exception:  # noqa: BLE001
        return None


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
        bias = "bullish" if rsi > 50 else "bearish"
        if rsi >= 70:
            bias, note = "bearish", "overbought"
        elif rsi <= 30:
            bias, note = "bullish", "oversold"
        else:
            note = ""
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
        out.append(_signal(
            "adx_14", adx,
            "bullish" if adx > 20 and (daily.get("plus_di_14") or 0) > (daily.get("minus_di_14") or 0)
            else ("bearish" if adx > 20 else "neutral"),
            note="trend strength: >20 meaningful" if adx <= 20 else "trending",
        ))
    cci = daily.get("cci_20")
    if cci is not None:
        out.append(_signal("cci_20", cci, "bullish" if cci > 0 else "bearish"))
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
            if rsi > 50:
                bull += 1
            else:
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


def _scenarios(tfs: Dict[str, Any], levels: Dict[str, Any], confluence: Dict[str, Any]) -> Dict[str, Any]:
    daily = tfs.get("daily") or {}
    if (
        not daily
        or "error" in daily
        or daily.get("current_price") is None
        or levels.get("status") != "ok"
    ):
        return _unsupported("daily data or levels unavailable")
    price = daily["current_price"]
    resistances = [z["level"] for z in levels.get("exit_zones", []) if z["kind"] == "resistance"]
    supports = [z["level"] for z in levels.get("entry_zones", []) if z["kind"] == "support"]
    res1 = resistances[0] if resistances else None
    sup1 = supports[0] if supports else None
    overall = confluence.get("overall", "neutral")

    def _prob(shares):
        # deterministic mapping from confluence share; no invented precision
        return shares

    bull_share = confluence.get("bullish_share")
    bear_share = _round(
        100 - (bull_share or 50) - (confluence.get("neutral") /
        max(confluence.get("total_signals") or 1, 1) * 100), 2
    ) if bull_share is not None else None

    return {
        "status": "ok",
        "bullish": {
            "trigger": f"close above resistance {res1}" if res1 else "sustained closes above current range high",
            "first_target": levels.get("targets", {}).get("tp2"),
            "weighting_hint": f"bullish signals {confluence.get('bullish')} of {confluence.get('total_signals')}",
        },
        "bearish": {
            "trigger": f"close below support {sup1}" if sup1 else "loss of the current range low",
            "first_target": levels.get("targets", {}).get("tp1"),
            "weighting_hint": f"bearish signals {confluence.get('bearish')} of {confluence.get('total_signals')}",
        },
        "neutral": {
            "trigger": "hold inside support/resistance band",
            "band": {"support": sup1, "resistance": res1},
            "weighting_hint": overall,
        },
        "bias_from_confluence": overall,
        "signal_shares_pct": {"bullish": bull_share, "bearish": bear_share},
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
    top = best[:3]
    for t in top:
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

def _catalysts(announcements: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if not announcements:
        return _unsupported("no announcements data available")
    recent = announcements[:8]
    items = [
        {
            "date": a.get("date") or a.get("broadcast_date"),
            "title": a.get("heading") or a.get("subject") or a.get("title"),
        }
        for a in recent
        if a.get("heading") or a.get("subject") or a.get("title")
    ]
    if not items:
        return _unsupported("announcements present but none have usable titles")
    return {
        "status": "ok",
        "note": "recent corporate announcements that may act as technical catalysts",
        "items": items,
    }


# --------------------------------------------------------------------------
# Main assembler
# --------------------------------------------------------------------------

REPORT_SECTIONS = [
    "executive_summary", "asset_overview", "current_price_market_data",
    "multi_timeframe_price_analysis", "trend_analysis", "market_structure",
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
    """Assemble the full 60-section report. Per-section failure degrades to
    an `unsupported` marker; the document itself always returns 200."""
    symbol = symbol.upper()
    source_u = source.upper()
    requested = [t.strip() for t in timeframes.split(",") if t.strip()]

    exchange = source_u if source_u != "SEC" else "NASDAQ"
    tfs: Dict[str, Any] = {}

    def run_collection():
        for tf in requested:
            if tf == "intraday":
                tfs["intraday"] = _intraday_snapshot(symbol, exchange)
                continue
            if tf not in _TIMEFRAMES:
                tfs[tf] = {"error": f"unknown timeframe '{tf}'"}
                continue
            rule, period = _TIMEFRAMES[tf]
            tfs[tf] = _collect_timeframe(symbol, exchange, tf, rule, period)
        return tfs

    tfs = await asyncio.to_thread(run_collection)

    # 1y daily frame, fetched once: relative strength, historical vol,
    # historical pattern comparison and the price-action candle all use it.
    hist_1y = await asyncio.to_thread(fetch_history, symbol, exchange, "1y")

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
            })
    sections["multi_timeframe_price_analysis"] = _ok(mtf_price)

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
    vol_payload = {k: daily.get(k) for k in ("obv", "acc_dist") if daily.get(k) is not None}
    sections["volume_analysis"] = _ok(vol_payload) if vol_payload else _unsupported("no volume data")
    sections["obv_accumulation_distribution"] = _ok(vol_payload) if vol_payload else _unsupported("no volume data")
    sections["volume_profile"] = (
        _ok(daily["structure"]["volume_profile"])
        if daily.get("structure", {}).get("volume_profile")
        else _unsupported("needs >= 20 bars with volume")
    )
    vwap_data = {"daily_anchored_vwap": daily.get("vwap_anchored")}
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
    sections["market_structure"] = _ok(structure["market_structure"]) if structure.get("market_structure") else _unsupported("insufficient bars")
    sections["support_resistance"] = _ok(structure["support_resistance"]) if structure.get("support_resistance") else _unsupported("insufficient bars")
    sections["supply_demand_zones"] = _ok(structure["supply_demand_zones"]) if structure.get("supply_demand_zones") else _unsupported("no zones detected")
    sections["candlestick_analysis"] = _ok(structure["candlesticks"]) if structure.get("candlesticks") else _unsupported("insufficient bars")
    sections["chart_pattern_analysis"] = _ok(structure["chart_patterns"]) if structure.get("chart_patterns") else _unsupported("insufficient bars")
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

    # ---- trend & regime (5, 33, 34) ----
    sections["trend_analysis"] = _ok({
        "market_structure": structure.get("market_structure", {}).get("classification"),
        "trend_regime": structure.get("trend_regime"),
        "sma_alignment": {
            "sma_20": daily.get("sma_20"),
            "sma_50": daily.get("sma_50"),
            "sma_200": daily.get("sma_200"),
        },
    }) if structure else _unsupported("insufficient bars")
    sections["market_regime"] = _ok(structure.get("trend_regime") or {}) if structure.get("trend_regime") else _unsupported("insufficient bars")
    sections["trend_vs_range"] = _ok(structure.get("trend_regime") or {}) if structure.get("trend_regime") else _unsupported("insufficient bars")

    # ---- signals & scenarios (35-36, 49-53) ----
    signals = _daily_signals(daily)
    confluence = _confluence(signals)
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

    scenarios = _scenarios(tfs, levels, confluence)
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
    weekly = tfs.get("weekly") or {}
    sections["medium_term_setup"] = _ok({
        "basis_timeframe": "weekly",
        "rsi_14": weekly.get("rsi_14"),
        "sma_20": weekly.get("sma_20"),
        "structure": (weekly.get("structure") or {}).get("market_structure", {}).get("classification"),
    }) if weekly and not weekly.get("error") else _unsupported("weekly timeframe unavailable")
    monthly = tfs.get("monthly") or {}
    sections["long_term_structure"] = _ok({
        "basis_timeframe": "monthly",
        "rsi_14": monthly.get("rsi_14"),
        "sma_10": monthly.get("sma_20"),
        "structure": (monthly.get("structure") or {}).get("market_structure", {}).get("classification"),
    }) if monthly and not monthly.get("error") else _unsupported("monthly timeframe unavailable")

    # ---- dashboards (53-54) ----
    sections["indicator_dashboard"] = _ok(signals) if signals else _unsupported("no price data")
    sections["mtf_signal_matrix"] = _ok(_mtf_matrix(tfs))

    # ---- 56, 57 ----
    sections["upcoming_catalysts"] = _catalysts(announcements)
    sections["historical_pattern_comparison"] = _historical_comparison(hist_1y)

    # ---- 58, 59 ----
    risk_factors = []
    if daily.get("adx_14") is not None and daily["adx_14"] < 20:
        risk_factors.append("ADX below 20: range regime — trend-following signals less reliable")
    if hv and hv.get("hv_20d_annualized_pct") and hv["hv_20d_annualized_pct"] > 60:
        risk_factors.append(f"20d realized volatility {hv['hv_20d_annualized_pct']}%: elevated — size positions accordingly")
    div = structure.get("divergence", {}).get("divergences") or []
    for d in div:
        risk_factors.append(f"{d['type']} divergence against current trend at {d['anchor']}")
    if not bench_ok:
        risk_factors.append("relative strength could not be computed — benchmark feed unavailable")
    sections["technical_risk_factors"] = _ok(risk_factors or ["no quantitative risk flags triggered"])

    sections["overall_assessment"] = _ok({
        "confluence": confluence["overall"],
        "regime": (structure.get("trend_regime") or {}).get("regime"),
        "structure": (structure.get("market_structure") or {}).get("classification"),
        "risk_reward_to_tp1": levels.get("risk_reward_to_tp1") if levels.get("status") == "ok" else None,
        "note": "deterministic synthesis of the sections above; not investment advice",
    })

    # ---- 1 & 60 ----
    sections["executive_summary"] = _ok({
        "symbol": symbol,
        "price": price,
        "confluence": confluence["overall"],
        "regime": (structure.get("trend_regime") or {}).get("regime"),
        "structure_class": (structure.get("market_structure") or {}).get("classification"),
        "sections_supported": sum(1 for s in sections.values() if isinstance(s, dict) and s.get("status") == "ok"),
        "sections_unsupported": sum(1 for s in sections.values() if isinstance(s, dict) and s.get("status") == "unsupported"),
    })
    sections["data_sources_methodology"] = _ok({
        "price_source": "Yahoo Finance (yfinance + chart fallback)",
        "filings_source": source_u,
        "indicator_definitions": "pandas-only implementations aligned with pandas_ta / Wilder smoothing (see src/tools/nse/technicals.py)",
        "patterns": "rule-based detectors over confirmed fractal pivots (see src/tools/nse/patterns.py)",
        "levels": "deterministic mapping from clustered S/R + Fibonacci + ATR; no discretion",
        "as_of": _utcnow(),
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

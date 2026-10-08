"""Price-structure analytics over OHLCV frames.

Pure functions over a pandas OHLCV frame — no network, no DB — so they are
trivially testable. Complements technicals.py, which computes indicator
last-values: here the *shape* of price is analysed (pivots, zones, patterns,
gaps, divergences).

Report sections served (professional end-to-end technical report layout):
    6 market structure · 7 support/resistance · 8 supply/demand zones
    10 candlestick analysis · 11 chart patterns · 12 breakout/breakdown
    22 volume profile · 32 gap analysis · 37 divergence analysis
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.tools.nse.technicals import _acc_dist, _adx, _rsi, _sma


# --------------------------------------------------------------------------
# Pivots
# --------------------------------------------------------------------------

def find_pivots(
    high: pd.Series,
    low: pd.Series,
    left: int = 3,
    right: int = 3,
) -> Dict[str, List[int]]:
    """Fractal pivot indices: a pivot high has `left` lower highs before and
    `right` lower highs after (mirror for lows). The final `right` bars can
    never confirm a pivot — that is inherent to the definition.
    """
    n = len(high)
    ph: List[int] = []
    pl: List[int] = []
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    for i in range(left, n - right):
        win_h = h[i - left : i + right + 1]
        win_l = l[i - left : i + right + 1]
        # Pivot high: rising into the bar, no strictly-higher bar in the
        # window. This treats a double-print top (two equal highs adjacent,
        # common on real data) as one pivot at the first bar, while a flat
        # stretch (every bar equal) produces none.
        if (
            h[i] == win_h.max()
            and (win_h > h[i]).sum() == 0
            and h[i] > h[i - 1]
        ):
            ph.append(i)
        if (
            l[i] == win_l.min()
            and (win_l < l[i]).sum() == 0
            and l[i] < l[i - 1]
        ):
            pl.append(i)
    return {"high": ph, "low": pl}


# --------------------------------------------------------------------------
# Support / resistance + supply / demand zones
# --------------------------------------------------------------------------

def _cluster_levels(values: List[float], tolerance_pct: float) -> List[Dict[str, Any]]:
    """Greedy clustering of pivot prices into levels, nearest first."""
    levels: List[Dict[str, Any]] = []
    for v in sorted(values):
        for lvl in levels:
            mid = lvl["price"]
            if abs(v - mid) / mid <= tolerance_pct:
                lvl["touches"] += 1
                # running mean keeps the level centred on its cluster
                lvl["price"] = round(
                    (mid * (lvl["touches"] - 1) + v) / lvl["touches"], 4
                )
                break
        else:
            levels.append({"price": round(v, 4), "touches": 1})
    return levels


def support_resistance(
    hist: pd.DataFrame,
    tolerance_pct: float = 0.015,
    max_levels: int = 8,
) -> Dict[str, Any]:
    """Clustered S/R levels from confirmed pivots, split around last close."""
    high, low = hist["High"], hist["Low"]
    pivots = find_pivots(high, low, 3, 3)
    prices = [high.iloc[i] for i in pivots["high"]] + [low.iloc[i] for i in pivots["low"]]
    if not prices:
        return {"supports": [], "resistances": [], "pivots_found": 0}
    clustered = _cluster_levels(prices, tolerance_pct)
    close = float(hist["Close"].iloc[-1])
    # cap each side separately: a global cap lets resistances (which
    # outnumber pivots below close in a crash) starve the support side
    supports = sorted(
        (l for l in clustered if l["price"] < close),
        key=lambda x: x["touches"], reverse=True,
    )[:max_levels]
    resistances = sorted(
        (l for l in clustered if l["price"] >= close),
        key=lambda x: x["touches"], reverse=True,
    )[:max_levels]
    supports.sort(key=lambda x: x["price"])
    resistances.sort(key=lambda x: x["price"])
    return {
        "supports": supports,
        "resistances": resistances,
        "pivots_found": len(pivots["high"]) + len(pivots["low"]),
    }


def supply_demand_zones(
    hist: pd.DataFrame,
    lookback: int = 90,
    max_zones: int = 4,
    zone_width_pct: float = 0.02,
) -> List[Dict[str, Any]]:
    """Zones anchored on the pivot bars of the biggest directional swings:
    a strong rally away from a base leaves demand below; a sell-off leaves
    supply above. Each zone is a price band around the pivot bar's range.
    """
    window = hist.tail(lookback)
    if len(window) < 20:
        return []
    pivots = find_pivots(window["High"], window["Low"], 5, 5)
    close = float(hist["Close"].iloc[-1])
    rows = []
    for i in pivots["low"]:
        move_fwd = window["Close"].iloc[i:].max() / window["Low"].iloc[i] - 1
        rows.append({
            "kind": "demand",
            "bar": i,
            "low": float(window["Low"].iloc[i]),
            "high": float(window["High"].iloc[i]),
            "strength": float(move_fwd),
        })
    for i in pivots["high"]:
        drop = 1 - window["Close"].iloc[i:].min() / window["High"].iloc[i]
        rows.append({
            "kind": "supply",
            "bar": i,
            "low": float(window["Low"].iloc[i]),
            "high": float(window["High"].iloc[i]),
            "strength": float(drop),
        })
    rows = [r for r in rows if r["strength"] > 0.03]
    rows.sort(key=lambda r: r["strength"], reverse=True)
    zones: List[Dict[str, Any]] = []
    for r in rows[: max_zones * 3]:
        # cap width: a wick bar (e.g. a -15% crash day) otherwise anchors a
        # zone spanning 20%+ of price that swallows the nearest resistance
        width = min(
            max(r["high"] - r["low"], r["low"] * zone_width_pct),
            close * 0.06,
        )
        zl, zh = r["low"], r["low"] + width
        # zones must sit on their own side of price: demand below, supply above
        if r["kind"] == "demand" and zh > close * 1.001:
            continue
        if r["kind"] == "supply" and zl < close * 0.999:
            continue
        # overlapping same-kind zones are the same level — keep the stronger
        if any(
            z["kind"] == r["kind"] and zl < z["zone_high"] and zh > z["zone_low"]
            for z in zones
        ):
            continue
        vol = (
            float(hist["Volume"].iloc[-len(hist) + r["bar"] :].sum())
            if "Volume" in hist.columns
            else None
        )
        zones.append({
            "kind": r["kind"],
            "zone_low": round(zl, 4),
            "zone_high": round(zh, 4),
            "strength_pct": round(r["strength"] * 100, 2),
            "volume_in_zone": vol,
        })
        if len(zones) >= max_zones:
            break
    return zones


# --------------------------------------------------------------------------
# Market structure & trend classification
# --------------------------------------------------------------------------

def market_structure(hist: pd.DataFrame) -> Dict[str, Any]:
    """HH/HL vs LH/LL classification from the last four confirmed pivots.

    `confidence` is low when the label rests on the minimum two pivot pairs
    or contradicts the SMA200 — two pivots alone overstate what they prove.
    """
    pivots = find_pivots(hist["High"], hist["Low"], 3, 3)
    ph, pl = pivots["high"], pivots["low"]
    counts = {"pivot_highs": len(ph), "pivot_lows": len(pl)}
    if len(ph) < 2 or len(pl) < 2:
        return {
            "classification": "insufficient_data",
            "swings": None,
            "pivot_count": counts,
            "confidence": "low",
            "price_vs_sma200": None,
        }
    last2_h = [float(hist["High"].iloc[i]) for i in ph[-2:]]
    last2_l = [float(hist["Low"].iloc[i]) for i in pl[-2:]]
    hh = last2_h[1] > last2_h[0]
    hl = last2_l[1] > last2_l[0]
    if hh and hl:
        cls = "uptrend (higher highs & higher lows)"
    elif not hh and not hl:
        cls = "downtrend (lower highs & lower lows)"
    else:
        cls = "range / transition (mixed pivots)"
    close = float(hist["Close"].iloc[-1])
    below_sma200 = None
    if len(hist) >= 200:
        sma200 = float(_sma(hist["Close"], 200).iloc[-1])
        if not np.isnan(sma200):
            below_sma200 = close < sma200
    conflict = (
        (cls.startswith("uptrend") and below_sma200 is True)
        or (cls.startswith("downtrend") and below_sma200 is False)
    )
    return {
        "classification": cls,
        "confidence": "low" if conflict or len(ph) < 3 or len(pl) < 3 else "high",
        "pivot_count": counts,
        "price_vs_sma200": (
            "below" if below_sma200 else "above"
        ) if below_sma200 is not None else None,
        "swings": {
            "pivot_highs": [round(v, 4) for v in last2_h],
            "pivot_lows": [round(v, 4) for v in last2_l],
        },
    }


def trend_regime(hist: pd.DataFrame) -> Dict[str, Any]:
    """Trend vs range: ADX 20 threshold + SMA20 slope confirmation."""
    close, high, low = hist["Close"], hist["High"], hist["Low"]
    if len(close) < 40:
        return {"regime": "insufficient_data", "adx": None, "sma20_slope_pct": None}
    adx_series, _, _ = _adx(high, low, close, 14)
    adx = float(adx_series.iloc[-1]) if not np.isnan(adx_series.iloc[-1]) else None
    sma20 = _sma(close, 20)
    slope = (
        float((sma20.iloc[-1] / sma20.iloc[-6] - 1) * 100)
        if not np.isnan(sma20.iloc[-6])
        else None
    )
    if adx is None:
        regime = "insufficient_data"
    elif adx >= 20 and slope is not None and abs(slope) > 0.1:
        regime = "trending " + ("up" if slope > 0 else "down")
    else:
        regime = "ranging"
    if regime.startswith("trending"):
        basis = f"ADX {adx:.1f} >= 20 (strength) + SMA20 slope {slope:+.2f}% (direction)"
    else:
        basis = (
            f"ADX {adx:.1f} < 20 or flat SMA20 slope — no confirmed trend"
            if adx is not None
            else "insufficient bars for ADX"
        )
    return {"regime": regime, "adx": round(adx, 2) if adx is not None else None,
            "sma20_slope_pct": round(slope, 3) if slope is not None else None,
            "basis": basis}


# --------------------------------------------------------------------------
# Candlestick patterns
# --------------------------------------------------------------------------


def candlestick_patterns(hist: pd.DataFrame, last_n: int = 10) -> Dict[str, Any]:
    """Rule-based detection over the last `last_n` completed candles.

    Returns per-candle pattern hits (with dates) plus a bullish/bearish tally.
    Multi-candle patterns detected on consecutive bars are the same event —
    a sliding window re-fires on every bar of a streak — so only the first
    occurrence is kept.
    """
    h = hist.tail(last_n + 3)
    o, hi, lo, cl = h["Open"], h["High"], h["Low"], h["Close"]
    body = (cl - o).abs()
    rng = (hi - lo).replace(0, np.nan)
    upper_wick = hi - pd.concat([o, cl], axis=1).max(axis=1)
    lower_wick = pd.concat([o, cl], axis=1).min(axis=1) - lo
    bull = cl > o
    bear = cl < o

    hits: List[Dict[str, Any]] = []
    n = len(h)
    multi = {"morning_star", "evening_star", "three_white_soldiers", "three_black_crows"}
    last_seen: Dict[str, int] = {}
    for i in range(2, n):
        day_patterns: List[str] = []
        if (
            bull.iloc[i] and bear.iloc[i - 1]
            and cl.iloc[i] > o.iloc[i - 1] and o.iloc[i] < cl.iloc[i - 1]
            and body.iloc[i] > body.iloc[i - 1]
        ):
            day_patterns.append("bullish_engulfing")
        if (
            bear.iloc[i] and bull.iloc[i - 1]
            and cl.iloc[i] < o.iloc[i - 1] and o.iloc[i] > cl.iloc[i - 1]
            and body.iloc[i] > body.iloc[i - 1]
        ):
            day_patterns.append("bearish_engulfing")
        close_pos = (cl.iloc[i] - lo.iloc[i]) / rng.iloc[i] if rng.iloc[i] else np.nan
        # hammer: long lower wick, close in the upper part of the range
        if (
            lower_wick.iloc[i] > 2 * body.iloc[i]
            and upper_wick.iloc[i] < body.iloc[i]
            and close_pos >= 0.6
        ):
            day_patterns.append("hammer")
        # shooting star: long upper wick, close rejected into the lower part
        if (
            upper_wick.iloc[i] > 2 * body.iloc[i]
            and lower_wick.iloc[i] < body.iloc[i]
            and close_pos <= 0.4
        ):
            day_patterns.append("shooting_star")
        if body.iloc[i] <= 0.1 * rng.iloc[i]:
            day_patterns.append("doji")
        # star patterns: big body, small body gapped away, then reversal
        if (
            body.iloc[i - 2] > 0.7 * rng.iloc[i - 2]
            and body.iloc[i - 1] < 0.3 * rng.iloc[i - 1]
            and bull.iloc[i - 2]
            and o.iloc[i - 1] < cl.iloc[i - 2]  # star gapped down
            and cl.iloc[i] > (o.iloc[i - 2] + cl.iloc[i - 2]) / 2
        ):
            day_patterns.append("morning_star")
        if (
            body.iloc[i - 2] > 0.7 * rng.iloc[i - 2]
            and body.iloc[i - 1] < 0.3 * rng.iloc[i - 1]
            and bear.iloc[i - 2]
            and o.iloc[i - 1] > cl.iloc[i - 2]  # star gapped up
            and cl.iloc[i] < (o.iloc[i - 2] + cl.iloc[i - 2]) / 2
        ):
            day_patterns.append("evening_star")
        if (
            i >= 3
            and all(bull.iloc[j] for j in (i - 2, i - 1, i))
            and all(
                body.iloc[j] > 0.3 * rng.iloc[j] for j in (i - 2, i - 1, i)
            )  # meaningful bodies, not noise
            and cl.iloc[i] > cl.iloc[i - 1] > cl.iloc[i - 2]
        ):
            day_patterns.append("three_white_soldiers")
        if (
            i >= 3
            and all(bear.iloc[j] for j in (i - 2, i - 1, i))
            and all(
                body.iloc[j] > 0.3 * rng.iloc[j] for j in (i - 2, i - 1, i)
            )
            and cl.iloc[i] < cl.iloc[i - 1] < cl.iloc[i - 2]
        ):
            day_patterns.append("three_black_crows")
        if day_patterns:
            kept = []
            for p in day_patterns:
                if p in multi:
                    if last_seen.get(p) != i - 1:
                        kept.append(p)
                    last_seen[p] = i
                else:
                    kept.append(p)
            if kept:
                hits.append({
                    "date": str(h.index[i])[:10],
                    "bar_offset_from_end": n - 1 - i,
                    "patterns": kept,
                })

    bullish = sum(
        1 for x in hits for p in x["patterns"]
        if p in ("bullish_engulfing", "hammer", "morning_star", "three_white_soldiers")
    )
    bearish = sum(
        1 for x in hits for p in x["patterns"]
        if p in ("bearish_engulfing", "shooting_star", "evening_star", "three_black_crows")
    )
    return {
        "hits": hits,
        "bullish_count": bullish,
        "bearish_count": bearish,
    }


# --------------------------------------------------------------------------
# Chart patterns (heuristic)
# --------------------------------------------------------------------------

def chart_patterns(hist: pd.DataFrame, lookback: int = 60) -> Dict[str, Any]:
    """Honest heuristics only: double top/bottom, head & shoulders shapes,
    and a simple breakout-vs-range test with volume confirmation."""
    window = hist.tail(lookback)
    if len(window) < 30:
        return {"patterns": [], "breakout": None}
    pivots = find_pivots(window["High"], window["Low"], 3, 3)
    ph = [float(window["High"].iloc[i]) for i in pivots["high"]]
    pl = [float(window["Low"].iloc[i]) for i in pivots["low"]]
    found: List[str] = []
    tol = 0.02
    if len(ph) >= 2 and abs(ph[-1] - ph[-2]) / ph[-2] <= tol:
        found.append("possible_double_top")
    if len(pl) >= 2 and abs(pl[-1] - pl[-2]) / pl[-2] <= tol:
        found.append("possible_double_bottom")
    if len(ph) >= 3:
        h, s1, s2 = ph[-3], ph[-2], ph[-1]
        if h > s1 * 1.01 and abs(s1 - s2) / s1 <= tol:
            found.append("possible_head_and_shoulders")
    if len(pl) >= 3:
        h, s1, s2 = pl[-3], pl[-2], pl[-1]
        if h < s1 * 0.99 and abs(s1 - s2) / s1 <= tol:
            found.append("possible_inverse_head_and_shoulders")

    close = float(hist["Close"].iloc[-1])
    range_high = float(window["High"].iloc[:-1].max())
    range_low = float(window["Low"].iloc[:-1].min())
    vol_col = "Volume" in hist.columns
    breakout = None
    if close > range_high:
        vol_ok = (
            bool(
                hist["Volume"].iloc[-1]
                > 1.5 * hist["Volume"].iloc[-21:-1].mean()
            )
            if vol_col
            else None
        )
        breakout = {
            "type": "breakout",
            "level": round(range_high, 4),
            "volume_confirmed": vol_ok,
        }
    elif close < range_low:
        vol_ok = (
            bool(
                hist["Volume"].iloc[-1]
                > 1.5 * hist["Volume"].iloc[-21:-1].mean()
            )
            if vol_col
            else None
        )
        breakout = {
            "type": "breakdown",
            "level": round(range_low, 4),
            "volume_confirmed": vol_ok,
        }
    # trading range: tight box with multiple touches on both sides
    if range_low and (range_high - range_low) / range_low < 0.15:
        near_high = int((window["High"].iloc[:-1] >= range_high * 0.99).sum())
        near_low = int((window["Low"].iloc[:-1] <= range_low * 1.01).sum())
        if near_high >= 2 and near_low >= 2:
            found.append("trading_range")
    # channel: linear-regression fit on closes (ponytail: R²>=0.7 straight-line
    # channel only; add parallel-band channels if a real chart skill needs them)
    if len(window) >= 30:
        x = np.arange(len(window), dtype=float)
        y = window["Close"].to_numpy(dtype=float)
        slope_c = float(np.polyfit(x, y, 1)[0])
        yhat = slope_c * x + float(np.polyfit(x, y, 1)[1])
        ss_res = float(((y - yhat) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
        if r2 >= 0.7:
            found.append("rising_channel" if slope_c > 0 else "falling_channel")
        # triangle: pivot highs and lows converge
        if len(ph) >= 3 and len(pl) >= 3:
            hs = float(np.polyfit(np.arange(len(ph), dtype=float), np.array(ph), 1)[0])
            ls = float(np.polyfit(np.arange(len(pl), dtype=float), np.array(pl), 1)[0])
            if ls > 0 and hs < ls:
                found.append("ascending_triangle")
            elif hs < 0 and ls > hs:
                found.append("descending_triangle")
    return {"patterns": found, "breakout": breakout}


# --------------------------------------------------------------------------
# Gap analysis
# --------------------------------------------------------------------------

def gap_analysis(hist: pd.DataFrame, max_gaps: int = 5) -> Dict[str, Any]:
    """Recent price gaps (open vs prior close) with fill status."""
    if len(hist) < 2:
        return {"gaps": []}
    gaps: List[Dict[str, Any]] = []
    o = hist["Open"]
    pc = hist["Close"].shift(1)
    for i in range(len(hist) - 1, 0, -1):
        if np.isnan(o.iloc[i]) or np.isnan(pc.iloc[i]):
            continue
        up = o.iloc[i] > pc.iloc[i] * 1.005
        dn = o.iloc[i] < pc.iloc[i] * 0.995
        if not (up or dn):
            continue
        # filled when a later bar's range covers the gap
        later = hist.iloc[i + 1 :]
        filled = bool(
            ((later["Low"] <= pc.iloc[i]) & (later["High"] >= o.iloc[i])).any()
        ) if len(later) else False
        gaps.append({
            "date": str(hist.index[i])[:10],
            "type": "gap_up" if up else "gap_down",
            "gap_low": round(float(pc.iloc[i]), 4),
            "gap_high": round(float(o.iloc[i]), 4),
            "gap_pct": round(float((o.iloc[i] / pc.iloc[i] - 1) * 100), 2),
            "filled": filled,
        })
        if len(gaps) >= max_gaps:
            break
    return {"gaps": gaps}


# --------------------------------------------------------------------------
# Volume profile
# --------------------------------------------------------------------------

def volume_profile(hist: pd.DataFrame, bins: int = 24) -> Dict[str, Any]:
    """Volume-at-price histogram with POC and value area (70%).

    Each bar's volume is spread evenly across the bins its [low, high]
    spans — dumping a whole bar into one bin makes the profile lumpy
    (a single 23M day owns one bin) and the POC unreliable.
    """
    if "Volume" not in hist.columns or len(hist) < 20:
        return {}
    window = hist.tail(120)
    lo, hi = float(window["Low"].min()), float(window["High"].max())
    if hi <= lo:
        return {}
    edges = np.linspace(lo, hi, bins + 1)
    profile = np.zeros(bins)
    lows = window["Low"].to_numpy(dtype=float)
    highs = window["High"].to_numpy(dtype=float)
    vols = window["Volume"].to_numpy(dtype=float)
    for bar_low, bar_high, v in zip(lows, highs, vols):
        if not v or np.isnan(v):
            continue
        first = min(max(int(np.searchsorted(edges, bar_low, side="right")) - 1, 0), bins - 1)
        last = min(max(int(np.searchsorted(edges, bar_high, side="left")) - 1, 0), bins - 1)
        if last < first:
            continue
        profile[first : last + 1] += v / (last - first + 1)
    total = profile.sum()
    poc_bin = int(profile.argmax())
    # expand around POC until 70% of volume is covered
    lo_b = hi_b = poc_bin
    covered = profile[poc_bin]
    while covered < 0.7 * total and (lo_b > 0 or hi_b < bins - 1):
        below = profile[lo_b - 1] if lo_b > 0 else -1
        above = profile[hi_b + 1] if hi_b < bins - 1 else -1
        if above >= below:
            hi_b += 1
            covered += profile[hi_b]
        else:
            lo_b -= 1
            covered += profile[lo_b]
    return {
        "poc": round(float((edges[poc_bin] + edges[poc_bin + 1]) / 2), 4),
        "value_area": {
            "low": round(float((edges[lo_b] + edges[lo_b + 1]) / 2), 4),
            "high": round(float((edges[hi_b] + edges[hi_b + 1]) / 2), 4),
        },
        "bins": [
            {"price_low": round(float(edges[i]), 4),
             "price_high": round(float(edges[i + 1]), 4),
             "volume": float(profile[i])}
            for i in range(bins)
        ],
    }


# --------------------------------------------------------------------------
# Divergence detection
# --------------------------------------------------------------------------

def divergence(hist: pd.DataFrame) -> Dict[str, Any]:
    """Regular RSI divergences between the last two confirmed pivots."""
    if len(hist) < 40:
        return {"divergences": []}
    rsi = _rsi(hist["Close"], 14)
    pivots = find_pivots(hist["High"], hist["Low"], 3, 3)
    out: List[Dict[str, Any]] = []
    for kind, idxs in (("price_high", pivots["high"]), ("price_low", pivots["low"])):
        if len(idxs) < 2:
            continue
        i1, i2 = idxs[-2], idxs[-1]
        p1, p2 = hist["Close"].iloc[i1], hist["Close"].iloc[i2]
        r1, r2 = rsi.iloc[i1], rsi.iloc[i2]
        if any(np.isnan(v) for v in (r1, r2)):
            continue
        if kind == "price_high" and p2 > p1 and r2 < r1:
            out.append({"type": "bearish_regular", "anchor": kind})
        if kind == "price_low" and p2 < p1 and r2 > r1:
            out.append({"type": "bullish_regular", "anchor": kind})
    return {"divergences": out}


# --------------------------------------------------------------------------
# Aggregate for the report engine
# --------------------------------------------------------------------------

def analyze(hist: pd.DataFrame) -> Dict[str, Any]:
    """One call returning every structure analysis for a timeframe frame."""
    return {
        "support_resistance": support_resistance(hist),
        "supply_demand_zones": supply_demand_zones(hist),
        "market_structure": market_structure(hist),
        "trend_regime": trend_regime(hist),
        "candlesticks": candlestick_patterns(hist),
        "chart_patterns": chart_patterns(hist),
        "gaps": gap_analysis(hist),
        "volume_profile": volume_profile(hist),
        "divergence": divergence(hist),
    }

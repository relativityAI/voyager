import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from loguru import logger

# ponytail: pandas_ta was dropped — it drags in numba/llvmlite (~60MB RSS
# and JIT-compiled kernels) for eight indicators that are trivial rolling
# windows in plain pandas. Keep formulas aligned with the pandas_ta
# implementations (Wilder smoothing for RSI/ATR) so values stay identical.
_yf_lock = threading.Lock()

_YF_SUFFIX_MAP = {
    "NSE": ".NS",
    "BSE": ".BO",
    "NASDAQ": "",
    "NYSE": "",
    "AMEX": "",
}


def _generate_yf_symbol(symbol: str, exchange: str) -> str:
    suffix = _YF_SUFFIX_MAP.get(exchange.upper(), f".{exchange.upper()}")
    return symbol + suffix


def _to_valid_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return None


_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 300  # 5 minutes

_RAW_CACHE: Dict[str, Dict[str, Any]] = {}
RAW_CACHE_TTL = 300  # 5 minutes

# Bound the caches: yfinance "info" dicts are large and every distinct
# symbol keeps a full 1y OHLCV frame alive. Unbounded growth across symbols
# is a slow leak on a 512MB instance.
_CACHE_MAX_ENTRIES = 64


def _cache_evict(store: dict) -> None:
    if len(store) <= _CACHE_MAX_ENTRIES:
        return
    for k in sorted(store, key=lambda k: store[k]["ts"])[: len(store) - _CACHE_MAX_ENTRIES]:
        store.pop(k, None)


# ---- indicator helpers (pandas-only replacements for pandas_ta) ----------


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = _ema(close, fast) - _ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def _bbands(close: pd.Series, length: int = 20, std: float = 2.0):
    mid = _sma(close, length)
    sd = close.rolling(length).std(ddof=1)
    return mid + std * sd, mid, mid - std * sd


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / length, adjust=False).mean()


def _stoch(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3, smooth_k: int = 3):
    lowest_low = low.rolling(k).min()
    highest_high = high.rolling(k).max()
    fast_k = 100.0 * (close - lowest_low) / (highest_high - lowest_low)
    k_line = fast_k.rolling(smooth_k).mean()
    return k_line, k_line.rolling(d).mean()


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))
    return (direction * volume).cumsum()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14):
    """Wilder's ADX with +DI / -DI. Returns (adx, plus_di, minus_di)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / length, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / length, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / length, adjust=False).mean() / atr
    di_sum = (plus_di + minus_di)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    dx = dx.where(di_sum != 0)  # avoid div-by-zero -> NaN, keep float dtype
    adx = dx.ewm(alpha=1.0 / length, adjust=False).mean()
    return adx, plus_di, minus_di


def _cci(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    sma_tp = tp.rolling(length).mean()
    mean_dev = tp.rolling(length).apply(lambda w: float(np.abs(w - w.mean()).mean()), raw=True)
    return (tp - sma_tp) / (0.015 * mean_dev)


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    hh = high.rolling(length).max()
    ll = low.rolling(length).min()
    rng = hh - ll
    wr = -100.0 * (hh - close) / rng
    return wr.where(rng != 0)  # degenerate flat range -> NaN, never +-inf


def _acc_dist(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Chaikin Accumulation/Distribution line: CLV-weighted volume cumsum."""
    hl_range = (high - low).where((high - low) != 0)  # NaN, keep float dtype
    clv = ((close - low) - (high - close)) / hl_range
    return ((clv * volume).fillna(0.0)).cumsum()


def _vwap_session(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Rolling 1-bar-anchored VWAP proxy on intraday frames; on daily frames
    callers compute the anchored (session-start) variant instead."""
    tp = (high + low + close) / 3.0
    vol_cum = volume.cumsum()
    vwap = (tp * volume).cumsum() / vol_cum
    return vwap.where(vol_cum != 0)  # avoid div-by-zero -> NaN, keep float dtype


def _hist_volatility(close: pd.Series, length: int = 20, bars_per_year: int = 252) -> pd.Series:
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(length).std(ddof=1) * math.sqrt(bars_per_year) * 100.0


def _ichimoku(high: pd.Series, low: pd.Series, close: pd.Series):
    def _mid(length: int) -> pd.Series:
        return (high.rolling(length).max() + low.rolling(length).min()) / 2.0

    tenkan = _mid(9)
    kijun = _mid(26)
    senkou_a = ((tenkan + kijun) / 2.0).shift(26)
    senkou_b = _mid(52).shift(26)
    chikou = close.shift(-26)
    return tenkan, kijun, senkou_a, senkou_b, chikou


def _fib_levels(high: pd.Series, low: pd.Series, lookback: int = 120) -> Dict[str, Any]:
    """Retracement levels from the dominant swing of the last `lookback` bars.
    `direction` records which way the swing ran so consumers know whether the
    levels are retracements of an up-move or a down-move."""
    window = high.tail(lookback)
    window_low = low.tail(lookback)
    swing_high = _to_valid_float(window.max())
    swing_low = _to_valid_float(window_low.min())
    if swing_high is None or swing_low is None or swing_high == swing_low:
        return {}
    swing_high_idx = window.idxmax()
    swing_low_idx = window_low.idxmin()
    direction = "up" if swing_high_idx >= swing_low_idx else "down"
    diff = swing_high - swing_low
    ratios = {
        "0.0%": 0.0,
        "23.6%": 0.236,
        "38.2%": 0.382,
        "50.0%": 0.5,
        "61.8%": 0.618,
        "78.6%": 0.786,
        "100.0%": 1.0,
    }
    levels = {}
    for label, r in ratios.items():
        if direction == "up":
            levels[label] = round(swing_high - diff * r, 4)
        else:
            levels[label] = round(swing_low + diff * r, 4)
    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "direction": direction,
        "levels": levels,
    }


def _get_cached(key: str) -> Optional[Dict[str, Any]]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data: Dict[str, Any]) -> None:
    _cache[key] = {"ts": time.time(), "data": data}
    _cache_evict(_cache)
    _cache_evict(_RAW_CACHE)


def _get_yf_raw(symbol: str, exchange: str) -> Tuple[Any, Any]:
    with _yf_lock:
        key = f"{symbol}:{exchange}"
        entry = _RAW_CACHE.get(key)
        if entry and (time.time() - entry["ts"]) < RAW_CACHE_TTL:
            return entry["data"]["ticker"], entry["data"]["hist"]

        yf_symbol = _generate_yf_symbol(symbol, exchange)
        try:
            ticker = yf.Ticker(yf_symbol)
        except Exception as exc:  # noqa: BLE001 - never crash a metrics call
            # The missing-yfinance bug hid here for a long time: a NameError
            # from yf.Ticker surfaced as a silently null price, not an error.
            logger.warning(f"yfinance Ticker() failed for {yf_symbol}: {exc!r}")
            return None, pd.DataFrame()
        try:
            hist = ticker.history(period="1y")
        except Exception as exc:  # noqa: BLE001 - rate limits should not crash callers
            logger.warning(f"yfinance history failed for {yf_symbol}: {exc!r}")
            hist = pd.DataFrame()
        if hist is None or hist.empty:
            logger.warning(
                f"yfinance returned no history for {yf_symbol} "
                f"(Yahoo often rate-limits shared/datacenter IPs)"
            )

        _RAW_CACHE[key] = {
            "ts": time.time(),
            "data": {"ticker": ticker, "hist": hist},
        }
        return ticker, hist


_chart_sess: Any = None


def _chart_price(yf_symbol: str) -> Optional[float]:
    """Last price straight from Yahoo's chart endpoint.

    yfinance's .info/.history need a cookie+crumb handshake that Yahoo
    blocks for shared/datacenter IPs (Render), which silently nulls the
    price. The /v8/chart endpoint answers without a crumb, so it survives
    that block. Uses curl_cffi, already a pinned dependency.
    """
    global _chart_sess
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{yf_symbol}?range=1d&interval=1d"
    )
    try:
        if _chart_sess is None:
            from curl_cffi import requests as _creq

            _chart_sess = _creq.Session(impersonate="chrome")
        r = _chart_sess.get(url, timeout=15)
        if r.status_code != 200:
            logger.warning(f"chart fallback HTTP {r.status_code} for {yf_symbol}")
            return None
        meta = (r.json()["chart"]["result"] or [{}])[0].get("meta") or {}
        return _to_valid_float(meta.get("regularMarketPrice"))
    except Exception as exc:  # noqa: BLE001 - never crash a metrics call
        logger.warning(f"chart fallback failed for {yf_symbol}: {exc!r}")
        return None


def fetch_price_info(symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
    ticker, hist = _get_yf_raw(symbol, exchange)
    info: Dict[str, Any] = {}
    if ticker is not None:
        with _yf_lock:
            try:
                info = ticker.info or {}
            except Exception as exc:  # noqa: BLE001 - rate limits should not crash callers
                logger.warning(
                    f"yfinance info failed for {symbol}.{exchange}: {exc!r}"
                )
                info = {}
    shares = _to_valid_float(info.get("sharesOutstanding"))
    current_price = _to_valid_float(
        info.get("currentPrice") or info.get("regularMarketPrice")
    )
    # Live info may lag or be blocked; the last *completed* close is the
    # next-best source. fetch_history() drops the NaN partial candle.
    if current_price is None and hist is not None and not hist.empty and "Close" in hist:
        valid_closes = hist["Close"].dropna()
        if not valid_closes.empty:
            current_price = _to_valid_float(valid_closes.iloc[-1])
    if current_price is None:
        current_price = _chart_price(_generate_yf_symbol(symbol, exchange))
    logger.info(
        f"[PRICE] {symbol}.{exchange} current_price={current_price} shares={shares}"
    )
    return {"current_price": current_price, "shares_outstanding": shares}


# A rate-limited (empty) response must not be cached for the full TTL: one
# Yahoo 429 would blank a symbol's report for 5 minutes. Empty frames get a
# short negative-cache so a burst of requests doesn't hammer Yahoo either.
NEG_CACHE_TTL = 20  # seconds
_HISTORY_ATTEMPTS = 2
_HISTORY_RETRY_DELAY = 1.0  # seconds


def fetch_history(
    symbol: str,
    exchange: str = "NSE",
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """Raw OHLCV history (cached). Partial candles (a live day whose OHLC is
    still NaN) are dropped so indicator last-values are never poisoned.

    A transient provider failure is retried once and, if still empty,
    negative-cached for NEG_CACHE_TTL — not RAW_CACHE_TTL — so the next
    request self-heals instead of re-serving the failure for 5 minutes."""
    key = f"{symbol}:{exchange}:hist:{period}:{interval}"
    with _yf_lock:
        entry = _RAW_CACHE.get(key)
        if entry:
            ttl = RAW_CACHE_TTL if not entry["data"].empty else NEG_CACHE_TTL
            if (time.time() - entry["ts"]) < ttl:
                return entry["data"]

    yf_symbol = _generate_yf_symbol(symbol, exchange)
    hist = pd.DataFrame()
    for attempt in range(_HISTORY_ATTEMPTS):
        try:
            with _yf_lock:
                ticker = yf.Ticker(yf_symbol)
                hist = ticker.history(period=period, interval=interval)
        except Exception as exc:  # noqa: BLE001 - never crash callers
            logger.warning(f"yfinance history failed for {yf_symbol}: {exc!r}")
            hist = pd.DataFrame()
        if hist is not None and not hist.empty:
            # Drop the live bar when Yahoo pre-allocates it with NaN OHLC:
            # every rolling indicator would otherwise read NaN at idx -1.
            hist = hist.dropna(subset=["Open", "High", "Low", "Close"], how="any")
        if hist is not None and not hist.empty:
            break
        if attempt < _HISTORY_ATTEMPTS - 1:
            time.sleep(_HISTORY_RETRY_DELAY)
    if hist is None or hist.empty:
        logger.warning(
            f"yfinance returned no history for {yf_symbol} "
            f"(Yahoo often rate-limits shared/datacenter IPs)"
        )
        hist = pd.DataFrame()

    with _yf_lock:
        _RAW_CACHE[key] = {"ts": time.time(), "data": hist}
        _cache_evict(_RAW_CACHE)
        _cache_evict(_cache)
    return hist


def _resample_ohlcv(hist: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Daily -> weekly/monthly OHLCV. 'W' keeps Sunday week-start labels
    (pandas default); periods are anchored on actual timestamps so the
    last-value extraction stays correct either way."""
    agg = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
    }
    if "Volume" in hist.columns:
        agg["Volume"] = "sum"
    # ponytail: yfinance returning nothing leaves a RangeIndex frame, which
    # resample() rejects; returning it keeps the caller's "no data" path.
    if not isinstance(hist.index, pd.DatetimeIndex):
        return hist
    return hist.resample(rule).agg(agg).dropna(subset=["Open", "Close"], how="any")


_TIMEFRAMES = {
    "daily": (None, "1y"),
    "weekly": ("W", "2y"),
    "monthly": ("ME", "5y"),
}


def fetch_technicals(
    symbol: str,
    exchange: str = "NSE",
    period: str = "1y",
    timeframe: str = "daily",
) -> Dict[str, Any]:
    """Indicator last-values on one timeframe. weekly/monthly resample the
    daily feed (no extra fetches); timeframes beyond `daily` are exposed by
    the /history and /technicals endpoints."""
    if timeframe not in _TIMEFRAMES:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Use: {list(_TIMEFRAMES)}")

    yf_key = f"{symbol}:{exchange}:technicals:{timeframe}"
    cached = _get_cached(yf_key)
    if cached is not None:
        return cached

    resample_rule, fetch_period = _TIMEFRAMES[timeframe]
    hist = fetch_history(symbol, exchange, period=fetch_period)
    if hist.empty:
        return {
            "current_price": None,
            "error": f"No price data for {symbol}.{exchange}",
        }
    if resample_rule is not None:
        hist = _resample_ohlcv(hist, resample_rule)
        if hist.empty:
            return {
                "current_price": None,
                "error": f"No resampled data for {symbol}.{exchange}",
            }

    current_price = _to_valid_float(hist["Close"].dropna().iloc[-1])

    technicals: Dict[str, Any] = {
        "current_price": current_price,
    }

    def _add(key: str, series, idx: int = -1) -> None:
        if series is None:
            return
        try:
            val = _to_valid_float(series.iloc[idx])
            if val is not None:
                technicals[key] = round(val, 4)
        except (IndexError, TypeError, AttributeError):
            pass

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"] if "Volume" in hist else None

    for period_len in [20, 50, 200]:
        if len(close) >= period_len:
            _add(f"sma_{period_len}", _sma(close, period_len))
    for period_len in [12, 26]:
        if len(close) >= period_len:
            _add(f"ema_{period_len}", _ema(close, period_len))
    if len(close) >= 14:
        _add("rsi_14", _rsi(close, 14))
    if len(close) >= 26:
        macd_line, signal_line, hist_line = _macd(close, 12, 26, 9)
        _add("macd", macd_line)
        _add("macd_signal", signal_line)
        _add("macd_hist", hist_line)
    if len(close) >= 20:
        bbu, bbm, bbl = _bbands(close, 20, 2.0)
        _add("bb_upper", bbu)
        _add("bb_middle", bbm)
        _add("bb_lower", bbl)
    if len(close) >= 14:
        _add("atr_14", _atr(high, low, close, 14))
        _add("williams_r_14", _williams_r(high, low, close, 14))
        adx, plus_di, minus_di = _adx(high, low, close, 14)
        _add("adx_14", adx)
        _add("plus_di_14", plus_di)
        _add("minus_di_14", minus_di)
    if len(close) >= 20:
        _add("cci_20", _cci(high, low, close, 20))
    if len(close) >= 14:
        k_line, d_line = _stoch(high, low, close, 14, 3, 3)
        _add("stoch_k", k_line)
        _add("stoch_d", d_line)
    if volume is not None and not volume.empty:
        _add("obv", _obv(close, volume))
        _add("acc_dist", _acc_dist(high, low, close, volume))
    if len(close) >= 26:
        tenkan, kijun, senkou_a, senkou_b, chikou = _ichimoku(high, low, close)
        _add("ichimoku_tenkan", tenkan)
        _add("ichimoku_kijun", kijun)
        _add("ichimoku_senkou_a", senkou_a)
        _add("ichimoku_senkou_b", senkou_b)
        # chikou is shifted into the future; guard the slice explicitly.
        if len(close) > 26:
            _add("ichimoku_chikou", chikou)

    # Bounded extra output (volume profile, fib, gaps) comes from
    # patterns.py — the raw frame is handed over there, not recomputed.
    technicals["bars"] = int(len(close))

    _set_cache(yf_key, technicals)
    return technicals


TECHNICALS_METRICS: List[Dict[str, Any]] = [
    {"id": "current_price", "name": "Current Price", "type": "price"},
    {"id": "adx_14", "name": "Average Directional Index (14)", "type": "trend"},
    {"id": "plus_di_14", "name": "Plus Directional Indicator (+DI 14)", "type": "trend"},
    {"id": "minus_di_14", "name": "Minus Directional Indicator (-DI 14)", "type": "trend"},
    {"id": "cci_20", "name": "Commodity Channel Index (20)", "type": "oscillator"},
    {"id": "williams_r_14", "name": "Williams %R (14)", "type": "oscillator"},
    {"id": "acc_dist", "name": "Accumulation/Distribution Line", "type": "volume"},
    {"id": "ichimoku_tenkan", "name": "Ichimoku Tenkan-sen (9)", "type": "price"},
    {"id": "ichimoku_kijun", "name": "Ichimoku Kijun-sen (26)", "type": "price"},
    {"id": "ichimoku_senkou_a", "name": "Ichimoku Senkou Span A", "type": "price"},
    {"id": "ichimoku_senkou_b", "name": "Ichimoku Senkou Span B", "type": "price"},
    {"id": "ichimoku_chikou", "name": "Ichimoku Chikou Span", "type": "price"},
    {"id": "sma_20", "name": "Simple Moving Average (20)", "type": "price"},
    {"id": "sma_50", "name": "Simple Moving Average (50)", "type": "price"},
    {"id": "sma_200", "name": "Simple Moving Average (200)", "type": "price"},
    {"id": "ema_12", "name": "Exponential Moving Average (12)", "type": "price"},
    {"id": "ema_26", "name": "Exponential Moving Average (26)", "type": "price"},
    {"id": "rsi_14", "name": "Relative Strength Index (14)", "type": "oscillator"},
    {"id": "macd", "name": "MACD Line", "type": "oscillator"},
    {"id": "macd_signal", "name": "MACD Signal Line", "type": "oscillator"},
    {"id": "macd_hist", "name": "MACD Histogram", "type": "oscillator"},
    {"id": "bb_upper", "name": "Bollinger Band Upper (20,2)", "type": "price"},
    {"id": "bb_middle", "name": "Bollinger Band Middle (20,2)", "type": "price"},
    {"id": "bb_lower", "name": "Bollinger Band Lower (20,2)", "type": "price"},
    {"id": "atr_14", "name": "Average True Range (14)", "type": "volatility"},
    {"id": "stoch_k", "name": "Stochastic %K (14,3,3)", "type": "oscillator"},
    {"id": "stoch_d", "name": "Stochastic %D (14,3,3)", "type": "oscillator"},
    {"id": "obv", "name": "On-Balance Volume", "type": "volume"},
]


def get_technicals_catalog() -> Dict[str, Any]:
    return {
        "id": "technicals",
        "name": "Technical Indicators",
        "type": "technical",
        "metrics": TECHNICALS_METRICS,
    }

import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

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
    if current_price is None and hist is not None and not hist.empty and "Close" in hist:
        valid_closes = hist["Close"].dropna()
        if not valid_closes.empty:
            current_price = _to_valid_float(valid_closes.iloc[-1])
    logger.info(
        f"[PRICE] {symbol}.{exchange} current_price={current_price} shares={shares}"
    )
    return {"current_price": current_price, "shares_outstanding": shares}


def fetch_technicals(
    symbol: str, exchange: str = "NSE", period: str = "1y"
) -> Dict[str, Any]:
    yf_key = f"{symbol}:{exchange}:technicals"
    cached = _get_cached(yf_key)
    if cached is not None:
        return cached

    ticker, hist = _get_yf_raw(symbol, exchange)

    if hist.empty:
        return {
            "current_price": None,
            "error": f"No price data for {symbol}.{exchange}",
        }

    info = {}
    with _yf_lock:
        try:
            info = ticker.info or {}
        except Exception as exc:  # noqa: BLE001 - rate limits should not crash callers
            logger.debug(f"yfinance info failed for {symbol}.{exchange}: {exc}")
    current_price = _to_valid_float(
        info.get("currentPrice") or info.get("regularMarketPrice")
    )
    if current_price is None:
        valid_closes = hist["Close"].dropna()
        current_price = (
            _to_valid_float(valid_closes.iloc[-1]) if not valid_closes.empty else None
        )

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
    if len(close) >= 14:
        k_line, d_line = _stoch(high, low, close, 14, 3, 3)
        _add("stoch_k", k_line)
        _add("stoch_d", d_line)
    if volume is not None and not volume.empty:
        _add("obv", _obv(close, volume))

    _set_cache(yf_key, technicals)
    return technicals


TECHNICALS_METRICS: List[Dict[str, Any]] = [
    {"id": "current_price", "name": "Current Price", "type": "price"},
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

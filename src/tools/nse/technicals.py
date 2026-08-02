import math
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas_ta as ta
import yfinance as yf

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


def _get_cached(key: str) -> Optional[Dict[str, Any]]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data: Dict[str, Any]) -> None:
    _cache[key] = {"ts": time.time(), "data": data}


def _get_yf_raw(symbol: str, exchange: str) -> Tuple[Any, Any]:
    key = f"{symbol}:{exchange}"
    entry = _RAW_CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < RAW_CACHE_TTL:
        return entry["data"]["ticker"], entry["data"]["hist"]

    yf_symbol = _generate_yf_symbol(symbol, exchange)
    ticker = yf.Ticker(yf_symbol)
    hist = ticker.history(period="1y")

    _RAW_CACHE[key] = {
        "ts": time.time(),
        "data": {"ticker": ticker, "hist": hist},
    }
    return ticker, hist


def fetch_price_info(symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
    ticker, hist = _get_yf_raw(symbol, exchange)
    info = ticker.info or {}
    shares = _to_valid_float(info.get("sharesOutstanding"))
    current_price = _to_valid_float(
        info.get("currentPrice") or info.get("regularMarketPrice")
    )
    if current_price is None and not hist.empty and "Close" in hist:
        valid_closes = hist["Close"].dropna()
        if not valid_closes.empty:
            current_price = _to_valid_float(valid_closes.iloc[-1])
    print(f"[PRICE] {symbol}.{exchange} current_price={current_price} shares={shares}")
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

    info = ticker.info or {}
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
            _add(f"sma_{period_len}", ta.sma(close, length=period_len))
    for period_len in [12, 26]:
        if len(close) >= period_len:
            _add(f"ema_{period_len}", ta.ema(close, length=period_len))
    if len(close) >= 14:
        _add("rsi_14", ta.rsi(close, length=14))
    if len(close) >= 26:
        macd = ta.macd(close, fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            for col in macd.columns:
                label = col.lower()
                if label.startswith("macd") and not any(
                    c in label for c in ["macdh", "macds"]
                ):
                    _add("macd", macd[col])
                elif "macdh" in label:
                    _add("macd_hist", macd[col])
                elif "macds" in label:
                    _add("macd_signal", macd[col])
    if len(close) >= 20:
        bb = ta.bbands(close, length=20, std=2)
        if bb is not None and not bb.empty:
            for col in bb.columns:
                label = col.lower()
                if label.startswith("bbu"):
                    _add("bb_upper", bb[col])
                elif label.startswith("bbm"):
                    _add("bb_middle", bb[col])
                elif label.startswith("bbl"):
                    _add("bb_lower", bb[col])
    if len(close) >= 14:
        _add("atr_14", ta.atr(high, low, close, length=14))
    if len(close) >= 14:
        stoch = ta.stoch(high, low, close, k=14, d=3, smooth_k=3)
        if stoch is not None and not stoch.empty:
            _add("stoch_k", stoch[stoch.columns[0]])
            if len(stoch.columns) > 1:
                _add("stoch_d", stoch[stoch.columns[1]])
    if volume is not None and not volume.empty:
        _add("obv", ta.obv(close, volume))

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

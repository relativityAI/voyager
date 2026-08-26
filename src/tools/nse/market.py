"""NSE market data fetchers — live quote + historical OHLCV.

Primary source: NSE's own ``quote-equity`` and ``historical/cm/equity``
endpoints.  Falls back to yfinance (``technicals.py``) when NSE is
unreachable, so price data is never fully blocked by upstream failures.

Historical depth: 3 years (S-06).
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

_HISTORICAL_DEPTH_YEARS = 3

_QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol={symbol}"
_HISTORICAL_URL = (
    "https://www.nseindia.com/api/historical/cm/equity"
    "?symbol={symbol}&from={from_date}&to={to_date}&select=normal"
)


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", ""))
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return None


def _parse_nse_quote(raw: dict) -> Dict[str, Any]:
    """Normalise an NSE ``quote-equity`` response into a flat dict."""
    info = raw.get("priceInfo", {})
    meta = raw.get("meta", {})
    trade_info = raw.get("tradeInfo", {})
    security = raw.get("securityInfo", {})
    pre_open = raw.get("preOpenMarket", {})

    return {
        "current_price": _safe_float(info.get("lastPrice")),
        "change": _safe_float(info.get("change")),
        "pct_change": _safe_float(info.get("pChange")),
        "open": _safe_float(info.get("open")),
        "high": _safe_float(info.get("intraDayHighLow", {}).get("max")),
        "low": _safe_float(info.get("intraDayHighLow", {}).get("min")),
        "previous_close": _safe_float(info.get("previousClose")),
        "volume": _safe_float(trade_info.get("totalTradedVolume")),
        "turnover": _safe_float(trade_info.get("totalTradedValue")),
        "delivery_pct": _safe_float(trade_info.get("deliveryToQty")),
        "high_52w": _safe_float(info.get("weekHighLow", {}).get("max")),
        "low_52w": _safe_float(info.get("weekHighLow", {}).get("min")),
        "market_cap": _safe_float(security.get("issuedSize")),  # total shares issued
        "isin": meta.get("isin"),
        "industry": meta.get("industry"),
        "series": meta.get("series"),
    }


def _parse_nse_historical(raw: dict) -> List[Dict[str, Any]]:
    """Normalise an NSE ``historical/cm/equity`` response into OHLCV rows."""
    rows: List[Dict[str, Any]] = []
    data = raw.get("data", [])
    if not isinstance(data, list):
        return rows
    for row in data:
        trade_date = row.get("Date") or row.get("date")
        if not trade_date:
            continue
        rows.append({
            "trade_date": trade_date,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "volume": _safe_float(row.get("TotalTradedVolume")),
            "turnover": _safe_float(row.get("TotalTradedValue")),
            "delivery_pct": _safe_float(row.get("DeliveryQty")),
        })
    return rows


def _parse_date(d: Any) -> Optional[date]:
    if isinstance(d, date):
        return d
    if isinstance(d, datetime):
        return d.date()
    try:
        return datetime.strptime(str(d), "%d-%b-%Y").date()
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def fetch_quote(api_client, symbol: str = "") -> Dict[str, Any]:
    """Fetch live quote + 52w high/low + delivery from NSE ``quote-equity``.

    ``api_client`` is an :class:`NSEApiClient` instance.
    Returns an empty dict on failure.
    """
    try:
        url = _QUOTE_URL.format(symbol=symbol)
        res = api_client._call(url, timeout=5)
        data = api_client._safe_json(res)
        if not data or not isinstance(data, dict):
            return {}
        return _parse_nse_quote(data)
    except Exception as exc:
        logger.debug(f"NSE quote fetch failed: {exc}")
        return {}


def fetch_historical(
    api_client,
    symbol: str,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Fetch up to 3 years of daily OHLCV from NSE ``historical/cm/equity``.

    ``api_client`` is an :class:`NSEApiClient` instance.
    Returns an empty list on failure.
    """
    try:
        if to_date is None:
            to_date = date.today()
        if from_date is None:
            from_date = to_date - timedelta(days=_HISTORICAL_DEPTH_YEARS * 365)
        url = _HISTORICAL_URL.format(
            symbol=symbol,
            from_date=from_date.strftime("%d-%m-%Y"),
            to_date=to_date.strftime("%d-%m-%Y"),
        )
        res = api_client._call(url, timeout=10)
        data = api_client._safe_json(res)
        if not data:
            return []
        return _parse_nse_historical(data)
    except Exception as exc:
        logger.debug(f"NSE historical fetch failed for {symbol}: {exc}")
        return []


__all__ = [
    "fetch_historical",
    "fetch_quote",
    "_parse_date",
    "_parse_nse_historical",
    "_parse_nse_quote",
    "_safe_float",
]
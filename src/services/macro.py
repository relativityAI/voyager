"""Country-scoped macro / market-structure data (country=in only).

Thin facade over the data repositories under ``research/projects/`` plus
direct NSE archive fetches. One async function per route, plain dicts out.
"""

import asyncio
import csv
import io
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ._common import (
    NotFoundError,
    ServiceUnavailableError,
    UnsupportedCountryError,
    UpstreamError,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NSEPY = str(_REPO_ROOT / "research" / "projects" / "nsepython")
_JUGAAD = str(_REPO_ROOT / "research" / "projects" / "jugaad-data")

for _p in (_NSEPY, _JUGAAD):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jugaad_data.nse.live import NSELive  # noqa: E402
from jugaad_data.rbi import RBI  # noqa: E402
from nsepython import (  # noqa: E402
    index_history,
    index_pe_pb_div,
    index_total_returns,
    nse_fiidii,
    nse_get_top_gainers,
    nse_get_top_losers,
    nse_optionchain_scrapper,
    nsefetch,
)

_IST = ZoneInfo("Asia/Kolkata")
_ALL_INDICES_URL = "https://www.nseindia.com/api/allIndices"
_CONS_CSV = "https://archives.nseindia.com/content/indices/ind_{stem}list.csv"
_VALUATION_CSV = "https://archives.nseindia.com/content/indices/nse-valuation.csv"

# ponytail: fixed point budget. Any span is thinned to <= this many bars, so a
# long range returns fewer rows with a bigger gap between dates. Short ranges
# (<= one trading year) pass through untouched. Dropped OHLC bars lose their
# intra-range high/low extremes; upgrade to pandas resample OHLC buckets if
# candle fidelity for multi-decade spans ever matters.
_TARGET_POINTS = 500


def _downsample(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Even-stride thin to ~_TARGET_POINTS, always keeping first and last."""
    if len(rows) <= _TARGET_POINTS:
        return rows
    step = math.ceil(len(rows) / _TARGET_POINTS)
    sampled = rows[::step]
    if sampled[-1] is not rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _require_country(country: str) -> str:
    if country.lower() != "in":
        raise UnsupportedCountryError(f"country={country!r} not yet connected; only 'in' is available")
    return "in"


def _now_iso() -> str:
    return datetime.now(_IST).isoformat(timespec="seconds")


def _today_iso() -> str:
    return date.today().isoformat()


def _parse_nse_date(raw: str) -> str:
    s = raw.strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unparseable NSE date {raw!r}")


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_index(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": row.get("indexSymbol"),
        "name": row.get("index"),
        "last": _to_float(row.get("last")),
        "change_pct": _to_float(row.get("percentChange")),
        "open": _to_float(row.get("open")),
        "high": _to_float(row.get("high")),
        "low": _to_float(row.get("low")),
        "previous_close": _to_float(row.get("previousClose")),
        "year_high": _to_float(row.get("yearHigh")),
        "year_low": _to_float(row.get("yearLow")),
        "pe": _to_float(row.get("pe")),
        "pb": _to_float(row.get("pb")),
        "dy": _to_float(row.get("dy")),
        "advances": _to_float(row.get("advances")),
        "declines": _to_float(row.get("declines")),
        "per_change_30d": _to_float(row.get("perChange30d")),
        "per_change_365d": _to_float(row.get("perChange365d")),
    }


async def _all_indices() -> List[Dict[str, Any]]:
    def _fetch():
        body = nsefetch(_ALL_INDICES_URL)
        data = (body or {}).get("data")
        if not data:
            raise UpstreamError("allIndices returned no data")
        return data

    return await asyncio.to_thread(_fetch)


async def _one_index(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper().strip()
    rows = await _all_indices()
    for row in rows:
        if (row.get("indexSymbol") or "").upper() == symbol:
            return {"row": row, "symbol": symbol}
    raise NotFoundError(
        f"index {symbol!r} not found; closest match: "
        f"{next((r.get('indexSymbol') for r in rows if r.get('indexSymbol')), '?')}"
    )


# --- resource async helpers --------------------------------------------------


async def _niftyindices_frame(kind: str, symbol: str, start: str, end: str):
    """Run one nsepython niftyindices query and 404 on unknown index."""

    def _run():
        if kind == "history":
            return index_history(symbol, start, end)
        if kind == "valuation":
            return index_pe_pb_div(symbol, start, end)
        return index_total_returns(symbol, start, end)

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:  # index unknown -> niftyindices HTML/empty page
        raise NotFoundError(f"index {symbol!r} has no {kind} line") from exc


async def _flows_json() -> List[Dict[str, Any]]:
    def _fetch():
        return nse_fiidii(mode="json") or []

    return await asyncio.to_thread(_fetch)


async def _turnover_segments() -> List[Dict[str, Any]]:
    def _fetch():
        body = NSELive().market_turnover()
        return (body or {}).get("data") or []

    return await asyncio.to_thread(_fetch)


# --- endpoint functions ------------------------------------------------------


async def macro_overview(country: str) -> Dict[str, Any]:
    _require_country(country)
    tops = {"NIFTY 50", "NIFTY NEXT 50", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 100", "NIFTY BANK", "NIFTY IT"}
    rows = await _all_indices()
    indices = [
        _row_to_index(r)
        for r in rows
        if (r.get("indexSymbol") or "").upper() in tops
    ]
    flows_date = flows_in = flows_dii = None
    for row in await _flows_json():
        cat = (row.get("category") or "").upper()
        if "FII" in cat:
            flows_in = _to_float(row.get("netValue"))
            try:
                flows_date = _parse_nse_date(row.get("date"))
            except Exception:
                flows_date = None
        elif "DII" in cat:
            flows_dii = _to_float(row.get("netValue"))
    turnover_cash_crore = None
    for seg in await _turnover_segments():
        if (seg.get("name") or "").lower() == "equities":
            day = seg.get("today") or seg.get("yesterday") or {}
            value = _to_float(day.get("value"))
            if value is not None:
                turnover_cash_crore = round(value / 1e7, 2)
    repo = None
    try:
        rates = await asyncio.to_thread(lambda: RBI().current_rates())
        repo = _to_float(str(rates.get("Policy Repo Rate", "")).rstrip("%"))
    except Exception:
        repo = None
    return {
        "country": "in",
        "as_of": _now_iso(),
        "indices": indices,
        "flows": {
            "date": flows_date,
            "fii_net_crore": flows_in,
            "dii_net_crore": flows_dii,
        },
        "turnover_cash_crore": turnover_cash_crore,
        "policy_repo_rate_pct": repo,
    }


async def macro_indices(country: str, limit: Optional[int] = None) -> Dict[str, Any]:
    _require_country(country)
    rows = await _all_indices()
    indices = [_row_to_index(r) for r in rows if r.get("indexSymbol")]
    if limit is not None:
        indices = indices[:limit]
    return {"country": "in", "as_of": _now_iso(), "count": len(indices), "indices": indices}


async def macro_index_quote(country: str, symbol: str) -> Dict[str, Any]:
    _require_country(country)
    found = await _one_index(symbol)
    return {"country": "in", "as_of": _now_iso(), "index": _row_to_index(found["row"])}


async def macro_index_history(
    country: str, symbol: str, start_date: Optional[str], end_date: Optional[str]
) -> Dict[str, Any]:
    _require_country(country)
    end = end_date or _today_iso()
    start = start_date or (date.today() - timedelta(days=365)).isoformat()
    df = await _niftyindices_frame("history", symbol, start, end)
    bars = []
    for _, row in df.iterrows():
        bars.append(
            {
                "date": _parse_nse_date(str(row["HistoricalDate"])),
                "open": _to_float(row["OPEN"]),
                "high": _to_float(row["HIGH"]),
                "low": _to_float(row["LOW"]),
                "close": _to_float(row["CLOSE"]),
            }
        )
    bars.sort(key=lambda b: b["date"])
    available = len(bars)
    bars = _downsample(bars)
    return {
        "country": "in",
        "symbol": symbol.upper(),
        "source": "niftyindices",
        "start_date": start,
        "end_date": end,
        "bars": bars,
        "bars_available": available,
        "downsampled": len(bars) < available,
    }


async def macro_index_valuation(
    country: str, symbol: str, start_date: Optional[str], end_date: Optional[str]
) -> Dict[str, Any]:
    _require_country(country)
    end = end_date or _today_iso()
    start = start_date or (date.today() - timedelta(days=365)).isoformat()
    df = await _niftyindices_frame("valuation", symbol, start, end)
    rows = []
    for _, row in df.iterrows():
        rows.append(
            {
                "date": _parse_nse_date(str(row["DATE"])),
                "pe": _to_float(row.get("pe")),
                "pb": _to_float(row.get("pb")),
                "dy": _to_float(row.get("divYield")),
            }
        )
    rows.sort(key=lambda r: r["date"])
    available = len(rows)
    rows = _downsample(rows)
    return {
        "country": "in",
        "symbol": symbol.upper(),
        "source": "niftyindices",
        "start_date": start,
        "end_date": end,
        "valuation": rows,
        "bars_available": available,
        "downsampled": len(rows) < available,
    }


async def macro_index_returns(
    country: str, symbol: str, start_date: Optional[str], end_date: Optional[str]
) -> Dict[str, Any]:
    _require_country(country)
    end = end_date or _today_iso()
    start = start_date or (date.today() - timedelta(days=365)).isoformat()
    df = await _niftyindices_frame("returns", symbol, start, end)
    # ponytail: NSE publishes a single total-return value per day (no OHLC), so
    # rows carry {date, close} only; open/high/low are absent = "not reported".
    rows = []
    for _, row in df.iterrows():
        rows.append(
            {
                "date": _parse_nse_date(str(row["Date"])),
                "close": _to_float(row["TotalReturnsIndex"]),
            }
        )
    rows.sort(key=lambda r: r["date"])
    available = len(rows)
    rows = _downsample(rows)
    return {
        "country": "in",
        "symbol": symbol.upper(),
        "source": "niftyindices",
        "start_date": start,
        "end_date": end,
        "bars": rows,
        "bars_available": available,
        "downsampled": len(rows) < available,
    }


_ALIAS_STEM = {"NIFTY FIN SERVICE": "niftyfinance", "NIFTY FINANCIAL SERVICES": "niftyfinance"}


def _constituents_stem(symbol: str) -> str:
    key = symbol.upper()
    if key in _ALIAS_STEM:
        return _ALIAS_STEM[key]
    return re.sub(r"[^a-z0-9]", "", key.lower())


async def macro_index_constituents(country: str, symbol: str) -> Dict[str, Any]:
    _require_country(country)
    symbol = symbol.upper().strip()
    stem = _constituents_stem(symbol)
    url = _CONS_CSV.format(stem=stem)

    def _fetch():
        import requests

        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            raise NotFoundError(
                f"no constituent file for {symbol!r} (`constituents_not_available`). "
                "Several thematic/strategy indices have no public constituent list."
            )
        return resp.text

    text = await asyncio.to_thread(_fetch)
    constituents = []
    for row in csv.DictReader(io.StringIO(text)):
        constituents.append(
            {
                "symbol": (row.get("Symbol") or "").strip(),
                "name": (row.get("Company Name") or "").strip(),
                "industry": (row.get("Industry") or "").strip(),
                "isin": (row.get("ISIN Code") or "").strip(),
            }
        )
    return {
        "country": "in",
        "symbol": symbol,
        "count": len(constituents),
        "constituents": constituents,
    }


async def macro_market_valuation(country: str, date_str: Optional[str]) -> Dict[str, Any]:
    _require_country(country)

    def _fetch():
        import requests

        resp = requests.get(_VALUATION_CSV, timeout=15)
        if resp.status_code != 200:
            raise ServiceUnavailableError(
                "whole-market P/E file no longer published by NSE archives "
                "(nse-valuation.csv returns 404)"
            )
        return resp.text

    text = await asyncio.to_thread(_fetch)
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        pe = _to_float(row.get("p/e") or row.get("P/E") or row.get("PE"))
        rows.append({"symbol": (row.get("SYMBOL") or "").strip(), "pe": pe})
    trade_date = date_str or max(
        ((r.get("DATE") or "").strip() for r in csv.DictReader(io.StringIO(text))),
        default=_today_iso(),
    )
    return {"country": "in", "trade_date": trade_date, "count": len(rows), "stocks": rows}


async def macro_breadth(country: str, limit: int) -> Dict[str, Any]:
    _require_country(country)
    rows = await _all_indices()
    index_breadth = []
    for r in rows:
        if not r.get("indexSymbol"):
            continue
        index_breadth.append(
            {
                "symbol": r["indexSymbol"],
                "advances": int(_to_float(r.get("advances")) or 0),
                "declines": int(_to_float(r.get("declines")) or 0),
            }
        )

    def _tops():
        gainers, losers = nse_get_top_gainers(), nse_get_top_losers()
        return gainers, losers

    gainers, losers = await asyncio.to_thread(_tops)

    def _to_list(df, limit_):
        out = []
        for _, r in df.head(limit_).iterrows():
            out.append(
                {
                    "symbol": str(r.get("symbol", "")),
                    "change_pct": _to_float(r.get("pChange")),
                    "last": _to_float(r.get("lastPrice")),
                }
            )
        return out

    # ponytail: 52-week high/low report endpoint removed from NSE API (404);
    # key omitted = "not reported". Re-add when a source lands.
    return {
        "country": "in",
        "date": _today_iso(),
        "index_breadth": index_breadth,
        "top_gainers": _to_list(gainers, limit),
        "top_losers": _to_list(losers, limit),
    }


async def macro_flows(country: str) -> Dict[str, Any]:
    _require_country(country)
    out = []
    for row in await _flows_json():
        cat = (row.get("category") or "").upper()
        out.append(
            {
                "category": "FII" if "FII" in cat else cat,
                "date": datetime.strptime(row["date"], "%d-%b-%Y").date().isoformat(),
                "equity_buy_crore": _to_float(row.get("buyValue")),
                "equity_sell_crore": _to_float(row.get("sellValue")),
                "equity_net_crore": _to_float(row.get("netValue")),
            }
        )
    return {"country": "in", "as_of": _now_iso(), "fii_dii": out}


async def macro_flows_fpi(country: str) -> Dict[str, Any]:
    _require_country(country)
    raise ServiceUnavailableError(
        "NSDL FPI data requires nselib.nsdl_fpi (headless Chromium via "
        "pyppeteer), not available in this environment. Retry hint: install "
        "pypeteer + browser, or use a proxy."
    )


async def macro_turnover(country: str) -> Dict[str, Any]:
    _require_country(country)
    segments = []
    for seg in await _turnover_segments():
        day = seg.get("today") or seg.get("yesterday") or {}
        value = _to_float(day.get("value"))
        segments.append(
            {
                "segment": seg.get("name"),
                "date": (
                    date.today().isoformat()
                    if seg.get("today")
                    else (date.today() - timedelta(days=1)).isoformat()
                ),
                "volume": _to_float(day.get("volume")),
                "value_crore": round(value / 1e7, 2) if value is not None else None,
                "open_interest": _to_float(day.get("openInterest")),
            }
        )
    return {"country": "in", "as_of": _now_iso(), "turnover": segments}


async def macro_derivatives(country: str, symbol: str) -> Dict[str, Any]:
    _require_country(country)
    underlying = symbol.upper()

    def _fetch():
        return nse_optionchain_scrapper(underlying)

    body = await asyncio.to_thread(_fetch)
    data = body.get("data") or []
    if not data:
        raise NotFoundError(f"no option chain for {underlying!r}")
    spot, ce_oi, pe_oi, ce_chg, pe_chg, maxpain, max_oi, expiry = None, 0.0, 0.0, 0.0, 0.0, None, -1.0, None
    for row in data:
        ce = row.get("CE") or {}
        pe = row.get("PE") or {}
        if ce.get("underlyingValue") is not None:
            spot = _to_float(ce.get("underlyingValue"))
        ce_oi += _to_float(ce.get("openInterest")) or 0.0
        pe_oi += _to_float(pe.get("openInterest")) or 0.0
        ce_chg += _to_float(ce.get("changeinOpenInterest")) or 0.0
        pe_chg += _to_float(pe.get("changeinOpenInterest")) or 0.0
        oi = (_to_float(ce.get("openInterest")) or 0.0) + (_to_float(pe.get("openInterest")) or 0.0)
        if oi > max_oi:
            max_oi, maxpain = oi, _to_float(row.get("strikePrice"))
        if not expiry:
            expiry = (ce.get("expiryDate") or pe.get("expiryDate")) or None
    total_oi = ce_oi + pe_oi
    change_oi = ce_chg + pe_chg
    prev_oi = total_oi - change_oi
    return {
        "country": "in",
        "underlying": underlying,
        "as_of": _now_iso(),
        "spot": spot,
        "expiry_date": expiry,
        "total_oi_lots": total_oi,
        "change_oi_pct": round(change_oi / prev_oi * 100, 2) if prev_oi else None,
        "put_call_ratio": round(pe_oi / ce_oi, 4) if ce_oi else None,
        "maxpain": maxpain,
    }


async def macro_rates(country: str) -> Dict[str, Any]:
    _require_country(country)

    def _fetch():
        return RBI().current_rates()

    raw = await asyncio.to_thread(_fetch)

    def _r(key):
        v = raw.get(key)
        if not v:
            return None
        return _to_float(str(v).rstrip("%"))

    rates = {
        "policy_repo_rate_pct": _r("Policy Repo Rate"),
        "savings_deposit_rate_pct": _r("Savings Deposit Rate"),
        "msf_rate_pct": _r("Marginal Standing Facility Rate"),
        "bank_rate_pct": _r("Bank Rate"),
        "fixed_reverse_repo_rate_pct": _r("Fixed Reverse Repo Rate"),
        "crr_pct": _r("CRR"),
        "slr_pct": _r("SLR"),
        "inr_per_usd": _r("INR / 1 USD"),
        "inr_per_gbp": _r("INR / 1 GBP"),
        "inr_per_eur": _r("INR / 1 EUR"),
        "inr_per_100_jpy": _r("INR / 100 JPY"),
    }
    return {
        "country": "in",
        "as_of": _today_iso(),
        "provider": "rbi",
        "rates": rates,
    }

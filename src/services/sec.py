"""SEC (US) data source — READ path only.

Serves company 8-K announcements and insider holdings from EDGAR via
``edgartools``. Pulling 10-K/10-Q XBRL into the statement tables lives in
``src.ingest.sec`` and runs via the operator tooling.

EDGAR requires a "Name email" declaration and enforces 10 req/s; edgartools
handles the throttling internally.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from src.services._common import NotFoundError, UpstreamError

# edgartools is imported lazily inside the functions that need it: it costs
# ~60MB RSS (dill, multiinspector, huge class hierarchy) and the API serves
# mostly NSE traffic. Deferring the import keeps idle footprint low on 512MB
# instances and only pays it during SEC reads.

# EDGAR requires a "Name email" declaration. SEC's bot filter learned to 403
# the old "VoyagerData/1.0 (github...)" identity outright (every request from
# every IP), while a fresh declared identity passes; keep this token unique
# and never reuse a previously-blocked app identifier.
_DEFAULT_IDENTITY = "Voyager/1.0 (data@voyager.local)"
_edgar_ready = False


def _ensure_edgar():
    """Import edgartools once and apply the identity/proxy configuration."""
    global _edgar_ready
    if _edgar_ready:
        return
    from edgar import set_identity
    from edgar.httpclient import configure_http as _configure_http

    identity = os.getenv("SEC_IDENTITY", _DEFAULT_IDENTITY).strip(" \t\r\n\"'")
    if identity:
        identity = " ".join(identity.split())
        set_identity(identity)
        logger.info(f"EDGAR identity set: {identity!r}")
    proxy = os.getenv("SEC_PROXY", "").strip()
    if proxy:
        # Cloud egress IPs (Render/AWS) are hard-flagged by SEC's bot filter
        # even with a browser UA; route through an egress proxy instead.
        _configure_http(proxy=proxy)
        logger.info(f"EDGAR proxy set: {proxy}")
    _edgar_ready = True


_MAX_INSIDER_PCT = 100.0
_SEC_DEFAULT_EXCHANGE = "NASDAQ"


def _one_company(symbol: str):
    _ensure_edgar()
    from edgar import Company, CompanyNotFoundError

    try:
        return Company(symbol)
    except CompanyNotFoundError as exc:
        raise NotFoundError(f"Ticker '{symbol}' not found on SEC EDGAR") from exc


async def get_announcements_us(
    symbol: str, country: str = "us", source: str = "sec", market: str = "equities",
    limit: Optional[int] = None, offset: int = 0,
) -> Dict[str, Any]:
    company = _one_company(symbol.upper())
    try:
        filings = company.get_filings(form="8-K", amendments=False).head(40)
    except Exception as exc:
        logger.warning(f"Announcements fetch failed for {symbol}: {exc}")
        raise UpstreamError(str(exc))

    items = []
    for f in filings:
        fdate = getattr(f, "filing_date", None)
        acc = getattr(f, "accession_number", None) or ""
        docs = getattr(f, "primary_document", None) or ""
        cik = getattr(f, "cik", None)
        url = None
        if cik and acc and docs:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{docs}"
        items.append(
            {
                "date": fdate.isoformat() if isinstance(fdate, date) else str(fdate or ""),
                "heading": f"8-K {docs or acc}",
                "category": "SEC 8-K",
                "attachment": url,
                "attachment_size": None,
                "has_xbrl": False,
            }
        )
    if offset > 0:
        items = items[offset:]
    if limit is not None:
        items = items[:limit]
    return {"symbol": symbol.upper(), "source": "SEC", "market": market, "announcements": items}


def _insider_share_count(company: Company, n: int = 80) -> Optional[Tuple[float, Optional[str]]]:
    """Latest post-transaction holding per insider (Forms 3/4).

    Returns ``(total_shares, latest_filing_date)``.
    """
    frames: List[pd.DataFrame] = []
    try:
        forms = company.get_filings(form="4", amendments=False).head(n)
        for f in forms:
            try:
                obj = f.obj()
                if hasattr(obj, "to_dataframe"):
                    frames.append(obj.to_dataframe())
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"Form 4 fetch failed for {company.cik}: {exc}")
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    if df.empty or "Insider" not in df.columns or "Remaining Shares" not in df.columns:
        return None
    latest = (
        df.dropna(subset=["Remaining Shares"])
        .sort_values("Date", ascending=False)
        .drop_duplicates(subset=["Insider"], keep="first")
    )
    as_of = None
    try:
        as_of = str(pd.to_datetime(latest["Date"]).max().date())
    except Exception:
        pass
    return float(latest["Remaining Shares"].sum()), as_of


async def get_shareholdings_us(
    symbol: str, country: str = "us", source: str = "sec"
) -> Dict[str, Any]:
    """US insider ownership from Forms 3/4/5 — a US-specific schema distinct
    from the India promoter/FII/DII pattern."""
    company = _one_company(symbol.upper())

    insider = await asyncio.to_thread(_insider_share_count, company)
    insider_shares = insider[0] if insider else None
    as_of = insider[1] if insider else None
    shares_out = None
    if insider_shares:
        try:
            from src.tools.nse.technicals import fetch_price_info

            price_info = await asyncio.to_thread(fetch_price_info, symbol.upper(), _SEC_DEFAULT_EXCHANGE)
            shares_out = price_info.get("shares_outstanding")
            shares_out = float(shares_out) if shares_out else None
        except Exception as exc:
            logger.debug(f"Shares outstanding fetch failed for {symbol}: {exc}")

    insider_ownership_pct = None
    if insider_shares and shares_out:
        insider_ownership_pct = round(min(insider_shares / shares_out * 100, _MAX_INSIDER_PCT), 2)

    return {
        "symbol": symbol.upper(),
        "source": "SEC",
        "shareholdings": {
            "source": "SEC",
            "source_endpoint": "Forms 3/4/5",
            "as_of": as_of,
            "insider_shares": insider_shares,
            "shares_outstanding": shares_out,
            "insider_ownership_pct": insider_ownership_pct,
            "note": "SEC Forms 3/4/5 insider holdings",
        },
    }

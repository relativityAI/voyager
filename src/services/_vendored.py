"""Vendored NSE/RBI scraping code from jugaad-data + nsepython.

Source: research/projects/{jugaad-data,nsepython} (gitignored — never shipped
to Render, so macro.py can't import the packages). MIT-licensed upstream.

ponytail: only the code paths macro.py actually calls. The full upstream libs
pull appdirs/scipy/playwright — not vendored, not needed. NSELive's live_cache
decorator is dropped: macro.py news up an instance per call, so the per-instance
5s cache never hits anyway.
"""

import json
import logging
import re
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

try:
    from curl_cffi.requests import Session as _CurlSession

    _CURL_CFFI_OK = True
except ImportError:
    _CURL_CFFI_OK = False


class NSEEndpointError(Exception):
    pass


# --- nseindia.com transport (nsepython/rahu.py) --------------------------------

headers = {
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.79 Safari/537.36",
    "Sec-Fetch-User": "?1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
}

_nse_session = None
_nse_warmed = False


def _get_nse_session():
    """Shared, warmed-up curl_cffi session (Akamai Bot Manager needs the TLS
    fingerprint + cookie warm-up)."""
    global _nse_session, _nse_warmed

    if not _CURL_CFFI_OK:
        raise ImportError(
            "curl_cffi is required to reach nseindia.com — plain requests gets "
            "blocked by Akamai Bot Manager on TLS fingerprint alone."
        )

    if _nse_session is None:
        _nse_session = _CurlSession(impersonate="chrome124")

    if not _nse_warmed:
        try:
            _nse_session.get("https://www.nseindia.com", headers=headers, timeout=20)
            time.sleep(1.2)
            _nse_session.get("https://www.nseindia.com/option-chain", headers=headers, timeout=20)
            time.sleep(0.8)
            _nse_warmed = True
        except Exception as e:
            logging.warning("NSE session warm-up failed/partial: %s", e)

    return _nse_session


def _equity_stockindices_fallback(session, api_headers):
    """/api/equity-stockIndices?index=SECURITIES IN F%26O is retired (404).
    /api/market-data-pre-open?key=FO carries the same per-symbol data —
    rewrite and reshape into the old {"data": [...]} shape."""
    r = session.get(
        "https://www.nseindia.com/api/market-data-pre-open?key=FO",
        headers=api_headers, timeout=30,
    )
    if r.status_code != 200:
        raise NSEEndpointError(
            f"equity-stockIndices fallback (market-data-pre-open) failed: HTTP {r.status_code}"
        )
    try:
        raw = r.json()
    except ValueError:
        raise NSEEndpointError("equity-stockIndices fallback returned a non-JSON body")

    reshaped = []
    for item in raw.get("data", []):
        m = item.get("metadata", {}) or {}
        if not m.get("symbol"):
            continue
        reshaped.append({
            "symbol": m.get("symbol", ""),
            "pChange": m.get("pChange", 0),
            "lastPrice": m.get("lastPrice", 0),
            "change": m.get("change", 0),
            "previousClose": m.get("previousClose", 0),
            "yearHigh": m.get("yearHigh", 0),
            "yearLow": m.get("yearLow", 0),
            "totalTradedValue": m.get("totalTurnover", 0),
            "totalTradedVolume": m.get("finalQuantity", 0),
        })
    return {"data": reshaped}


def nsefetch(payload: str):
    """Fetch a nseindia.com JSON API URL through the warmed-up session,
    retrying once with a fresh warm-up if the first attempt looks blocked."""
    global _nse_warmed

    session = _get_nse_session()
    api_headers = dict(headers)
    api_headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/option-chain",
    })

    if "equity-stockIndices" in payload and "SECURITIES" in payload:
        return _equity_stockindices_fallback(session, api_headers)

    try:
        r = session.get(payload, headers=api_headers, timeout=30)
        if r.status_code in (401, 403, 404, 429, 503):
            _nse_warmed = False
            session = _get_nse_session()
            r = session.get(payload, headers=api_headers, timeout=30)

        if r.status_code != 200:
            raise NSEEndpointError(f"nsefetch: HTTP {r.status_code} for {payload}")

        try:
            return r.json()
        except ValueError:
            raise NSEEndpointError(
                f"nsefetch: NSE returned a non-JSON body (length={len(r.text)}) for {payload}"
            )
    except NSEEndpointError:
        raise
    except Exception as e:
        raise NSEEndpointError(f"nsefetch: request failed for {payload}: {e}")


# --- nsepython functions macro.py calls ----------------------------------------


def nsesymbolpurify(symbol):
    return symbol.replace("&", "%26")  # URL parse for stocks like M&M Finance


def nse_optionchain_scrapper(symbol):
    symbol = nsesymbolpurify(symbol)
    url = f"https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolDerivativesData&symbol={symbol}"
    payload = nsefetch(url)

    if payload and "data" in payload:
        combined = {}
        for entry in payload["data"]:
            sp = entry.get("strikePrice")
            ed = entry.get("expiryDate")
            ot = entry.get("optionType")
            if not sp or not ed or ot == "XX":
                continue
            key = (sp, ed)
            if key not in combined:
                combined[key] = {"strikePrice": sp, "expiryDate": ed, "CE": None, "PE": None}
            combined[key][ot] = entry
        payload["data"] = list(combined.values())

    return payload


def nse_fiidii(mode="pandas"):
    try:
        if mode == "pandas":
            return pd.DataFrame(nsefetch("https://www.nseindia.com/api/fiidiiTradeReact"))
        return nsefetch("https://www.nseindia.com/api/fiidiiTradeReact")
    except Exception:
        logger.info("Pandas is not working for some reason.")
        return nsefetch("https://www.nseindia.com/api/fiidiiTradeReact")


def nse_get_top_losers():
    positions = nsefetch("https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O")
    df = pd.DataFrame(positions["data"])
    df = df.sort_values(by="pChange")
    return df.head(5)


def nse_get_top_gainers():
    positions = nsefetch("https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O")
    df = pd.DataFrame(positions["data"])
    df = df.sort_values(by="pChange", ascending=False)
    return df.head(5)


# --- niftyindices.com history (nsepython/rahu.py) ------------------------------

niftyindices_headers = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": "https://www.niftyindices.com",
    "Referer": "https://www.niftyindices.com/reports/historical-data",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="130", "Google Chrome";v="130"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_niftyindices_session = None
_niftyindices_warmed = False


def _get_niftyindices_session():
    global _niftyindices_session, _niftyindices_warmed
    if _niftyindices_session is None:
        _niftyindices_session = requests.Session()
    if not _niftyindices_warmed:
        try:
            _niftyindices_session.get(
                "https://www.niftyindices.com/reports/historical-data",
                headers=niftyindices_headers, timeout=15,
            )
            _niftyindices_warmed = True
        except Exception as e:
            logging.warning("niftyindices.com session warm-up failed/partial: %s", e)
    return _niftyindices_session


def _niftyindices_fetch(endpoint, symbol, start_date, end_date):
    session = _get_niftyindices_session()
    data = {"cinfo": "{'name':'" + symbol + "','startDate':'" + start_date + "','endDate':'" + end_date + "','indexName':'" + symbol + "'}"}
    response = session.post(
        f"https://www.niftyindices.com/BackPage/{endpoint}",
        headers=niftyindices_headers, json=data, timeout=20,
    )
    text = response.text.strip()
    if text.startswith("<!DOCTYPE") or text.startswith("<html") or text == "":
        raise NSEEndpointError(
            f"niftyindices.com/BackPage/{endpoint} returned HTML/empty instead of JSON "
            f"(HTTP {response.status_code}) -- the site may be down or have changed again."
        )
    try:
        payload = response.json()
    except ValueError:
        raise NSEEndpointError(
            f"niftyindices.com/BackPage/{endpoint}: non-JSON body (HTTP {response.status_code})"
        )
    # Old API wrapped the payload as {"d": "<json string>"}; the new one
    # returns the array directly. Support both.
    if isinstance(payload, dict) and "d" in payload:
        payload = json.loads(payload["d"])
    return pd.DataFrame.from_records(payload)


def index_history(symbol, start_date, end_date):
    return _niftyindices_fetch("getHistoricaldatatabletoString", symbol, start_date, end_date)


def index_pe_pb_div(symbol, start_date, end_date):
    return _niftyindices_fetch("getpepbHistoricaldataDBtoString", symbol, start_date, end_date)


def index_total_returns(symbol, start_date, end_date):
    return _niftyindices_fetch("getTotalReturnIndexString", symbol, start_date, end_date)


# --- jugaad_data.nse.live.NSELive.market_turnover -----------------------------


class NSELive:
    base_url = "https://www.nseindia.com/api"
    page_url = "https://www.nseindia.com/get-quotes/equity?symbol=LT"
    _routes = {
        "market_turnover": "/market-turnover",
    }

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "Host": "www.nseindia.com",
            "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=SBIN",
            "X-Requested-With": "XMLHttpRequest",
            "pragma": "no-cache",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.6998.166 Safari/537.36",
            "Sec-CH-UA": '"Google Chrome";v="134", "Chromium";v="134", "Not?A_Brand";v="99"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        })
        self.s.get(self.page_url)

    def get(self, route, payload={}):
        url = self.base_url + self._routes[route]
        r = self.s.get(url, params=payload)
        return r.json()

    def market_turnover(self):
        return self.get("market_turnover")


# --- jugaad_data.rbi.RBI.current_rates -----------------------------------------


def _tr_to_json(wrapper):
    trs = wrapper.find_all("tr")
    op = {}
    for tr in trs:
        tds = tr.find_all("td")
        if len(tds) >= 2:
            key = tds[0].text.strip()
            val = tds[1].text.replace(":", "").replace("*", "").replace("#", "").strip()
            op[key] = val
    return op


def _extract_rates_from_tables(bs):
    rates = {}

    for table in bs.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                key = cells[0].get_text().strip()
                value = cells[1].get_text().strip()
                value = value.replace(":", "").replace("*", "").replace("#", "").strip()
                if key and value and not key.lower().startswith("table"):
                    rates[key] = value

    rate_mappings = {
        "Policy Repo Rate": "Policy Repo Rate",
        "Standing Deposit Facility Rate": "Savings Deposit Rate",
        "Marginal Standing Facility Rate": "Marginal Standing Facility Rate",
    }

    text_content = bs.get_text()
    tbill_match = re.search(r"91.*day.*T-bill[s]?.*?(\d+\.?\d*%)", text_content, re.IGNORECASE)
    if tbill_match:
        rates["91 day T-bills"] = tbill_match.group(1)

    final_rates = {}
    for original_key, mapped_key in rate_mappings.items():
        if original_key in rates:
            final_rates[mapped_key] = rates[original_key]
    for key, value in rates.items():
        if key not in rate_mappings:
            final_rates[key] = value
    return final_rates


class RBI:
    base_url = "https://www.rbi.org.in/"

    def __init__(self):
        self.s = requests.Session()

    def current_rates(self):
        r = self.s.get(self.base_url)
        bs = BeautifulSoup(r.text, "html.parser")
        rates = _extract_rates_from_tables(bs)
        if not rates:
            wrapper = bs.find("div", {"id": "wrapper"})
            if wrapper:
                rates = _tr_to_json(wrapper)
        return rates

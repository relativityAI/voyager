# Exchange
"""
tags = [
        "toDate",
        "Symbol",
        "RevenueFromOperations",
        "OtherIncome",
        "Income",
        "FinanceCosts",
        "OtherExpenses",
        "Expenses",
        "ProfitBeforeExceptionalItemsAndTax",
        "ExceptionalItemsBeforeTax",
        "ProfitBeforeTax",
        "CurrentTax",
        "DeferredTax",
        "TaxExpense",
        "ProfitLossForPeriodFromContinuingOperations",
        "ProfitLossFromDiscontinuedOperationsBeforeTax",
        "TaxExpenseOfDiscontinuedOperations",
        "ProfitLossFromDiscontinuedOperationsAfterTax",
        "ProfitLossForPeriod",
        "ProfitOrLossAttributableToOwnersOfParent",
        "ComprehensiveIncomeForThePeriod",
        "PaidUpValueOfEquityShareCapital",
        "FaceValueOfEquityShareCapital",
        "BasicEarningsLossPerShareFromContinuingOperations",
        "DilutedEarningsLossPerShareFromContinuingOperations",
        "BasicEarningsLossPerShareFromDiscontinuedOperations",
        "DilutedEarningsLossPerShareFromDiscontinuedOperations",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "SegmentRevenueFromOperations",
        "SegmentProfitLossBeforeTaxAndFinanceCosts",
        "SegmentFinanceCosts",
        "SegmentRevenue",
        "InterSegmentRevenue",
        "SegmentProfitBeforeTax",
        "SegmentAssets",
        "UnAllocableAssets",
        "NetSegmentAssets",
        "SegmentLiabilities",
        "UnAllocableLiabilities",
        "NetSegmentLiabilities",
        "NoncurrentInvestments",
        "TradeReceivablesNoncurrent",
        "LoansNoncurrent",
        "OtherNoncurrentFinancialAssets",
        "NoncurrentFinancialAssets",
        "DeferredTaxAssetsNet",
        "OtherNoncurrentAssets",
        "NoncurrentAssets",
        "CapitalWorkInProgress",
        "InvestmentProperty",
        "Goodwill",
        "OtherIntangibleAssets",
        "Assets",
        "EquityShareCapital",
        "OtherEquity",
        "DebtEquityRatio",
        "CashAndCashEquivalents",
        "BankBalanceOtherThanCashAndCashEquivalents",
        "NoncurrentLiabilities",
        "BorrowingsCurrent",
        "CashFlowsFromUsedInOperations",
        "CashFlowsFromUsedInOperatingActivities"
            ]

"""

import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Dict
from urllib.parse import urlparse

from lxml import etree
from pypdf import PdfReader

from src.scrapers.session import (
    BlockedResponse,
    CookieError,
    SessionExhausted,
    StealthSession,
)
from src.scrapers.pipeline import proxy_resolver_for
from src.scrapers.sources.nse import (
    NSE_ENDPOINTS as ENDPOINTS,
)
from src.scrapers.sources.nse import (
    NSE_REFERER_BASE,
    build_nse_config,
)
from src.tools.nse.ratios import FINANCIAL_FIELD_MAP
from src.utils.case_converter import camel_to_snake

XBRLI_NS = "http://www.xbrl.org/2003/instance"

CAML_TO_SNAKE: Dict[str, str] = {k: camel_to_snake(k) for k in FINANCIAL_FIELD_MAP}

CATEGORY_MAP: Dict[str, str] = {}
for camel_tag, field in FINANCIAL_FIELD_MAP.items():
    cat = field.get("category", "")
    snake = camel_to_snake(camel_tag)
    CATEGORY_MAP[snake] = cat


def get_random_symbol():
    symbols = [
        "ADANIENT",
        "ADANIPORTS",
        "APOLLOHOSP",
        "ASIANPAINT",
        "AXISBANK",
        "BAJAJ-AUTO",
        "BAJFINANCE",
        "BAJAJFINSV",
        "BEL",
        "BHARTIARTL",
        "CIPLA",
        "COALINDIA",
        "DRREDDY",
        "EICHERMOT",
        "ETERNAL",
        "GRASIM",
        "HCLTECH",
        "HDFCBANK",
        "HDFCLIFE",
        "HINDALCO",
        "HINDUNILVR",
        "ICICIBANK",
        "INDIGO",
        "INFY",
        "ITC",
        "JIOFIN",
        "JSWSTEEL",
        "KOTAKBANK",
        "LT",
        "M&M",
        "MARUTI",
        "MAXHEALTH",
        "NESTLEIND",
        "NTPC",
        "ONGC",
        "POWERGRID",
        "RELIANCE",
        "SBILIFE",
        "SHRIRAMFIN",
        "SBIN",
        "SUNPHARMA",
        "TCS",
        "TATACONSUM",
        "TATAMOTORS",
        "TATASTEEL",
        "TECHM",
        "TITAN",
        "TRENT",
        "ULTRACEMCO",
        "WIPRO",
    ]
    return random.choice(symbols)


SH_PERCENTAGE_TAGS = {"ShareholdingAsAPercentageOfTotalNumberOfShares"}

SH_CONTEXT_TO_FIELD = {
    "ShareholdingOfPromoterAndPromoterGroupI": "promoters_and_promoter_group",
    "InstitutionsForeignI": "foreign_institutional_investors",
    "InstitutionsDomesticI": "domestic_institutional_investors",
    "NonInstitutionsI": "non_institutions",
    "PublicShareholdingI": "public_shareholding",
    "SharesHeldByNonPromoterNonPublicShareholdersI": "non_promoter_non_public_shareholding",
}

shareholding_context_ref_types = list(SH_CONTEXT_TO_FIELD.keys())

quarterly_context_ref_types = ["OneD", "OneI"]
annual_context_ref_types = ["FourD"]


class NSEApiClient:
    """Thin facade over the stealth transport for NSE.

    The public surface is unchanged from the pre-refactor client so callers
    (services, core, CLI, MCP) keep working. All anti-detection behaviour
    (TLS impersonation, cookie priming + persistence, throttling, retries,
    response validation) lives in :class:`StealthSession` (D-01..D-08).
    """

    def __init__(self, calls_per_second: float = 10.0):
        self.exchange = "nse"
        self.base = "https://www.nseindia.com"
        self.share_url_format = NSE_REFERER_BASE
        self.endpoints = ENDPOINTS
        self.quarterly_context_ref_types = quarterly_context_ref_types
        self.annual_context_ref_types = annual_context_ref_types
        self.shareholding_context_ref_types = shareholding_context_ref_types
        self.logger = logging.getLogger(__name__)

        config = build_nse_config(calls_per_second)
        self.session = StealthSession(config, proxy_resolver=proxy_resolver_for(config))

    def _referer_for(self, symbol: str) -> str:
        """Real NSE page used as the in-page Referer for API calls (D-05)."""
        return self.share_url_format.format(symbol=symbol or get_random_symbol())

    def _set_cookies(self, symbol: str, timeout=5) -> bool:
        """Prime the session cookie. Kept for API/test compatibility."""
        return self.session.prime(force=True)

    def _validate_api_response(self, resp) -> None:
        ctype = resp.headers.get("content-type", "")
        if "text/html" in ctype.lower():
            raise BlockedResponse(f"API returned HTML (blocked/not-ready): {ctype}")

    def _validate_download(self, resp) -> None:
        ctype = resp.headers.get("content-type", "")
        if "text/html" in ctype.lower():
            raise BlockedResponse(f"Download returned HTML (blocked/not-ready): {ctype}")

    def _call(self, url, symbol=None, max_call_attempts=3, timeout=5, referer=None):
        if not symbol:
            symbol = get_random_symbol()
        referer = referer or self._referer_for(symbol)
        try:
            return self.session.request(
                "GET",
                url,
                referer=referer,
                timeout=timeout,
                validate=self._validate_api_response,
            )
        except CookieError:
            raise
        except SessionExhausted as exc:
            self.logger.warning(f"NSE request exhausted retries: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 - surface as None, like before
            self.logger.error(f"NSE request failed for {url}: {exc}")
            return None

    def fetch_xbrl_content(self, url, symbol):
        if not url or url in (
            "-",
            "null",
            "https://nsearchives.nseindia.com/corporate/xbrl/-",
        ):
            return None
        response = self.fetch_url_content(url, symbol=symbol)
        if response:
            return response
        return None

    # API wrappers
    def _safe_json(self, res):
        if not res:
            return {}
        try:
            return res.json()
        except Exception as e:
            self.logger.error(f"JSON decode error: {e}")
            return {}

    def announcements_xbrls(self, symbol):
        res = self._call(
            self.endpoints["announcements-equities"].format(symbol=symbol.upper())
        )
        return self._safe_json(res)

    def annual_results_xbrls(self, symbol):
        res = self._call(
            self.endpoints["integrated-filing"].format(symbol=symbol.upper())
        )
        return self._safe_json(res)

    def quarterly_results_xbrls(self, symbol):
        res = self._call(
            self.endpoints["quarterly-results"].format(symbol=symbol.upper())
        )
        return self._safe_json(res)

    def integrated_filing_xbrls(self, symbol):
        res = self._call(
            self.endpoints["integrated-filing"].format(symbol=symbol.upper())
        )
        return self._safe_json(res)

    def shareholding_xbrls(self, symbol):
        res = self._call(
            self.endpoints["shareholding-pattern"].format(symbol=symbol.upper())
        )
        return self._safe_json(res)

    def annual_reports_xbrls(self, symbol):
        res = self._call(self.endpoints["annual-reports"].format(symbol=symbol.upper()))
        return self._safe_json(res)

    def fetch_url_content(self, url, symbol=None, referer=None):
        if not url:
            return None
        try:
            resp = self.session.request(
                "GET",
                url,
                referer=referer or f"{self.base}/",
                timeout=self.session.timeout,
                validate=self._validate_download,
            )
        except (CookieError, SessionExhausted) as exc:
            self.logger.error(f"Failed to fetch {url}: {exc}")
            return None
        if resp.status_code == 200:
            return resp.content
        self.logger.warning(f"Got {resp.status_code} fetching {url}")
        return None


class NSEDataParser:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def extract_xml(self, content: bytes, symbol: str):
        try:
            tree = etree.parse(BytesIO(content))
            root = tree.getroot()

            rows = []
            period_end_date = None
            contexts: Dict[str, Dict[str, str]] = {}
            units: Dict[str, str] = {}

            for elem in root.iter():
                tag = etree.QName(elem.tag).localname
                ns = etree.QName(elem.tag).namespace
                text = elem.text.strip() if elem.text else None

                if tag == "context" and ns == XBRLI_NS:
                    ctx_id = elem.get("id")
                    ctx_data: Dict[str, str] = {}
                    for child in elem.iter():
                        ct = etree.QName(child.tag).localname
                        if child.text:
                            val = child.text.strip()
                            if ct in ("identifier",):
                                ctx_data["entity"] = val
                            elif ct in ("instant", "startDate", "endDate"):
                                ctx_data[ct] = val
                    contexts[ctx_id] = ctx_data
                    continue

                if tag == "unit" and ns == XBRLI_NS:
                    for child in elem.iter():
                        ct = etree.QName(child.tag).localname
                        if ct == "measure" and child.text:
                            units[elem.get("id")] = child.text.strip()
                    continue

                if tag in ("xbrl",):
                    continue

                if tag == "endDate" and text:
                    period_end_date = text

                if ns and text:
                    rows.append(
                        {
                            "tag": tag,
                            "value": text,
                            "contextRef": elem.get("contextRef"),
                        }
                    )

            currency = next(iter(units.values()), None)

            for row in rows:
                cr = row.get("contextRef")
                ctx = contexts.get(cr, {})
                row["entity"] = ctx.get("entity", symbol)
                row["start_date"] = ctx.get("startDate")
                row["end_date"] = ctx.get("endDate")
                row["instant_date"] = ctx.get("instant")

            return {
                "symbol": symbol,
                "period_end_date": period_end_date,
                "financials": rows,
                "currency": currency,
                "contexts": contexts,
            }
        except Exception as e:
            self.logger.error(f"Extraction error: {e}")
            return None


class NSEIndia:
    def __init__(self, calls_per_second: float = 10.0):
        self.api = NSEApiClient(calls_per_second)
        self.parser = NSEDataParser()
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _derive_fiscal_period(period_end: str) -> str:
        try:
            dt = datetime.strptime(period_end, "%Y-%m-%d")
            m = dt.month
            if m in (1, 2, 3):
                return "Q4"
            elif m in (4, 5, 6):
                return "Q1"
            elif m in (7, 8, 9):
                return "Q2"
            else:
                return "Q3"
        except (ValueError, TypeError):
            return ""

    @staticmethod
    def _get_context_ref_type(context_ref: str) -> str:
        if not context_ref:
            return ""
        if any(ref in context_ref for ref in quarterly_context_ref_types):
            return "quarterly"
        if any(ref in context_ref for ref in annual_context_ref_types):
            return "annual"
        if any(ref in context_ref for ref in shareholding_context_ref_types):
            return "shareholding"
        return context_ref

    @staticmethod
    def _filter_facts_by_period(financials: list, category: str) -> list:
        period_tag = (
            "quarterly"
            if category in ("integrated-filing", "quarterly-results", "integrated")
            else "annual"
            if category == "annual-results"
            else None
        )
        if period_tag is None:
            return financials

        result = []
        for f in financials:
            start = f.get("start_date")
            end = f.get("end_date")
            instant = f.get("instant_date")
            point_in_time = bool(instant and not start)

            if point_in_time:
                result.append(f)
                continue

            # Q4 integrated filings publish the cash-flow statement only as a
            # full-year figure; dropping it left the cash_flows table empty.
            is_cash_flow_fact = f.get("tag", "").startswith("CashFlowsFromUsedIn")
            ctx_type = NSEIndia._get_context_ref_type(f.get("contextRef"))

            if ctx_type in ("quarterly", "annual"):
                if period_tag == "quarterly" and (
                    ctx_type == "quarterly" or is_cash_flow_fact
                ):
                    result.append(f)
                elif period_tag == "annual" and ctx_type == "annual":
                    result.append(f)
                continue

            if start and end:
                try:
                    sd = datetime.strptime(start, "%Y-%m-%d")
                    ed = datetime.strptime(end, "%Y-%m-%d")
                    days = (ed - sd).days
                    is_quarterly = 60 < days <= 130
                    is_annual = days >= 300
                except (ValueError, TypeError):
                    is_quarterly = False
                    is_annual = False

                if period_tag == "quarterly" and (
                    is_quarterly or (is_annual and is_cash_flow_fact)
                ):
                    result.append(f)
                elif period_tag == "annual" and is_annual:
                    result.append(f)
            else:
                result.append(f)

        return result

    def process_xbrl(self, x, symbol, category):
        try:
            xbrl_url = x.get("xbrl") or x.get("broadCastDate")
            if not xbrl_url or xbrl_url in ("-", "null"):
                return None

            broadcast_date = (
                x.get("broadcastDate") or x.get("broadcast_Date") or x.get("broadCastDate")
            )

            self.logger.debug(f"Processing XBRL for {symbol} ({category}): {xbrl_url}")
            extension = xbrl_url.split(".")[-1]
            if extension == "xml":
                t0 = time.perf_counter()
                content = self.api.fetch_xbrl_content(xbrl_url, symbol)
                download_ms = (time.perf_counter() - t0) * 1000
                if content:
                    t1 = time.perf_counter()
                    data = self.parser.extract_xml(content, symbol)
                    parse_ms = (time.perf_counter() - t1) * 1000
                    self.logger.debug(
                        f"XBRL {symbol} ({category}) download={download_ms:.0f}ms parse={parse_ms:.0f}ms"
                    )
                    if data and data.get("period_end_date"):
                        data["financials"] = self._filter_facts_by_period(
                            data["financials"], category
                        )

                        if category == "shareholding-pattern":
                            for f in data["financials"]:
                                cr = f.get("contextRef") or ""
                                if "_Context" in cr:
                                    f["contextRef"] = cr.replace("_Context", "")
                            data["financials"] = [
                                f
                                for f in data["financials"]
                                if f.get("contextRef") in shareholding_context_ref_types
                            ]

                        if not any(f.get("contextRef") for f in data["financials"]):
                            self.logger.warning(
                                f"No matching facts found in XBRL for {symbol} (category={category}), skipping"
                            )
                            return None

                        default_consolidated = (
                            "Shareholding"
                            if category == "shareholding-pattern"
                            else "Consolidated"
                        )
                        consolidated = x.get("consolidated", default_consolidated)
                        period_end = data["period_end_date"]

                        is_cons = str(consolidated).lower() in (
                            "consolidated",
                            "true",
                            "1",
                        )

                        if category in ("integrated-filing", "quarterly-results"):
                            filing_type = "quarterly"
                        elif category == "annual-results":
                            filing_type = "annual"
                        elif category == "shareholding-pattern":
                            filing_type = "shareholding"
                        else:
                            filing_type = "quarterly"

                        base_meta = {
                            "symbol": symbol.upper(),
                            "period_end_date": period_end,
                            "period_start_date": None,
                            "xbrl_url": xbrl_url,
                            "broadcast_date": broadcast_date,
                            "consolidated": is_cons,
                            "filing_type": filing_type,
                            "measure": data.get("currency"),
                            "entity_identifier": symbol.upper(),
                            "fiscal_period": self._derive_fiscal_period(period_end),
                            "source_endpoint": category,
                            "pulled_at": datetime.utcnow(),
                        }

                        for f in data["financials"]:
                            if (
                                f.get("start_date")
                                and base_meta["period_start_date"] is None
                            ):
                                base_meta["period_start_date"] = f["start_date"]

                        result: Dict[str, Any] = {
                            "income_statement": None,
                            "balance_sheet": None,
                            "cash_flow": None,
                            "shareholding": None,
                        }

                        stmts: Dict[str, list] = {
                            "income_statement": [],
                            "balance_sheet": [],
                            "cash_flow": [],
                            "shareholding": [],
                        }

                        ctx_ref_types_used: set = set()
                        for f in data["financials"]:
                            cr = f.get("contextRef", "")
                            ctx_ref_types_used.add(self._get_context_ref_type(cr))

                            tag = f["tag"]
                            tag_snake = CAML_TO_SNAKE.get(tag, camel_to_snake(tag))
                            cat = CATEGORY_MAP.get(tag_snake, "other")

                            if category == "shareholding-pattern" and cr:
                                if tag not in SH_PERCENTAGE_TAGS:
                                    continue
                                field = SH_CONTEXT_TO_FIELD.get(cr, camel_to_snake(cr))
                                stmts["shareholding"].append((field, f["value"]))
                                continue

                            entry = (tag_snake, f["value"])

                            if cat == "income_statement" or cat == "per_share":
                                stmts["income_statement"].append(entry)
                            elif cat == "balance_sheet":
                                stmts["balance_sheet"].append(entry)
                            elif cat == "cash_flow":
                                stmts["cash_flow"].append(entry)
                            elif cat == "metadata":
                                pass
                            else:
                                stmts["income_statement"].append(entry)

                        ctx_ref_type_str = (
                            ", ".join(sorted(ctx_ref_types_used))
                            if ctx_ref_types_used
                            else ""
                        )
                        base_meta["context_ref_type"] = ctx_ref_type_str

                        for stmt_key in (
                            "income_statement",
                            "balance_sheet",
                            "cash_flow",
                            "shareholding",
                        ):
                            entries = stmts[stmt_key]
                            if not entries:
                                continue
                            doc = dict(base_meta)
                            doc["_content_hash"] = hashlib.md5(
                                json.dumps(
                                    {
                                        k: doc[k]
                                        for k in (
                                            "symbol",
                                            "period_end_date",
                                            "consolidated",
                                            "source_endpoint",
                                        )
                                    },
                                    sort_keys=True,
                                    default=str,
                                ).encode()
                            ).hexdigest()
                            for tag_snake, value in entries:
                                doc[tag_snake] = value
                            result[stmt_key] = doc

                        stmt_types = [k for k, v in result.items() if v is not None]
                        self.logger.debug(
                            f"Parsed XBRL for {symbol} ({category}): {', '.join(stmt_types)}"
                        )
                        return result
            elif extension == "html":
                self.logger.error("HTML data extraction yet to be implemented")
            elif extension == "pdf":
                self.logger.error("PDF data extraction yet to be implemented")

        except Exception as e:
            self.logger.error(f"Error processing XBRL: {e}")
        return None

    def read_nse_document(self, url: str, symbol: str = None) -> str:
        if not url:
            return None
        parsed = urlparse(url)
        _, ext = os.path.splitext(parsed.path)
        if ext.lower() != ".pdf":
            self.logger.error(f"Unsupported document type: {ext}")
            return None
        content = self.api.fetch_url_content(
            url, symbol=symbol, referer="https://www.nseindia.com/"
        )
        if not content:
            self.logger.error(f"Failed to fetch document: {url}")
            return None
        try:
            reader = PdfReader(BytesIO(content))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
        except Exception as e:
            self.logger.error(f"Error reading PDF: {e}")
            return None

    async def download_announcements(self, symbol: str):
        pass

    async def download_shareholdings(self, symbol: str):
        pass

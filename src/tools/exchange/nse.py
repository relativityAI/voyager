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

import logging
import random
from io import BytesIO

from lxml import etree

from src.utils.rate_limiter import RateLimitedSession, get_rate_limiter
from src.utils.web import generate_fake_headers


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


ENDPOINTS = {
    "corp-info": "https://www.nseindia.com/api/corp-info?symbol={symbol}&corpType=corpInfo&market=equities",
    "shareholding-pattern": "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={symbol}",
    "announcements-equity": "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}",
    "announcements-sme": "https://www.nseindia.com/api/corporate-announcements?index=sme&symbol={symbol}",
    "annual-reports": "https://www.nseindia.com/api/annual-reports?index=equities&symbol={symbol}",
    "event-calendar": "https://www.nseindia.com/api/event-calendar",
    "quarterly-results": "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={symbol}&period=Quarterly",
    "annual-results": "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={symbol}&period=Annual",
    "integrated-filing": "https://www.nseindia.com/api/integrated-filing-results?&symbol={symbol}",
}

quarterly_context_ref_types = ["OneD", "OneI"]
annual_context_ref_types = ["FourD"]
shareholding_context_ref_types = [
    "ShareholdingOfPromoterAndPromoterGroupI",
    "InstitutionsForeignI",
    "InstitutionsDomesticI",
    "NonInstitutionsI",
]


class NSEIndia:
    def __init__(self, calls_per_second: float = 10.0):
        """
        Initialize NSE India scraper with rate limiting.

        Args:
            calls_per_second: Maximum API calls per second (default: 10)
        """
        self.exchange = "nse"
        self.base = "https://www.nseindia.com"
        self.share_url_format = (
            "https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
        )
        self.endpoints = ENDPOINTS
        self.quarterly_context_ref_types = quarterly_context_ref_types
        self.annual_context_ref_types = annual_context_ref_types
        self.shareholding_context_ref_types = shareholding_context_ref_types

        # Use rate-limited session for API calls
        self.session = RateLimitedSession(
            calls_per_second=calls_per_second, service_name="nse_india"
        )
        self.rate_limiter = get_rate_limiter("nse_india", calls_per_second)
        self.headers = generate_fake_headers()
        self.logger = logging.getLogger(__name__)

    def _set_cookies(self, symbol: str, timeout=10):
        url = self.share_url_format.format(symbol=symbol)
        try:
            self.headers = generate_fake_headers()
            self.session.get(url, headers=self.headers, timeout=timeout)
        except Exception as e:
            self.logger.error(f"Failed to set cookies: {e}")

    def _call(self, url, symbol=None, max_call_attempts=3, timeout=10):
        if not symbol:
            symbol = get_random_symbol()
        for _ in range(max_call_attempts):
            if not self.session.cookies:
                self._set_cookies(symbol)
            try:
                response = self.session.get(url, headers=self.headers, timeout=timeout)
                if response.status_code == 200:
                    return response
            except:
                pass
                self._set_cookies(symbol)
        return None

    def extract(self, url, symbol):
        if not url or url in (
            "-",
            "null",
            "https://nsearchives.nseindia.com/corporate/xbrl/-",
        ):
            return None
        extension = url.split(".")[-1]

        try:
            response = self._call(url, symbol=symbol)
            content = None
            if response and response.status_code == 200:
                content = BytesIO(response.content)

            if not content:
                self.logger.error(f"Could not fetch xbrl : {url}")
                return

            if extension == "xml":
                tree = etree.parse(content)
                root = tree.getroot()

                nsmap = root.nsmap.copy()
                if None in nsmap:
                    nsmap["default"] = nsmap.pop(None)

                rows = []
                date = None
                for elem in root.iter():
                    tag = etree.QName(elem.tag).localname
                    ns = etree.QName(elem.tag).namespace
                    if tag in ("context", "unit", "xbrl"):
                        continue
                    text = elem.text.strip() if elem.text else None

                    if tag == "endDate":
                        date = text

                    if ns and text:
                        rows.append(
                            {
                                # 'namespace': ns,
                                "tag": tag,
                                "value": text,
                                "contextRef": elem.get("contextRef"),
                                # 'unitRef': elem.get('unitRef'),
                                # 'decimals': elem.get('decimals')
                            }
                        )

                # for row in rows:
                #     row['date'] = date
                #     row['symbol'] = symbol

                data = {"symbol": symbol, "date": date, "financials": rows}

                return data

            elif extension == "html":
                logging.error("HTML data extraction yet to be implemented")
            elif extension == "pdf":
                logging.error("PDF data extraction yet to be implemented")

            return item
        except Exception as e:
            self.logger.error(f"Extraction error: {e}")
            return None

    # High-level methods moved from CLI
    async def download_financials(self, symbol: str):
        self.logger.info(f"Downloading financials for {symbol}")
        integrated = self.integrated_filing_xbrls(symbol)
        if integrated and "data" in integrated:
            for x in integrated["data"]:
                await self._process_xbrl(x, symbol, "integrated")

        quarterly = self.quarterly_results_xbrls(symbol)
        if quarterly:
            for x in quarterly:
                await self._process_xbrl(x, symbol, "quarterly")

    async def _process_xbrl(self, x, symbol, category):
        xbrl_url = x.get("xbrl") or x.get("broadCastDate")  # fallback
        if not xbrl_url or xbrl_url in ("-", "null"):
            return

        date_str = x.get("broadcast_Date") or x.get("broadCastDate")
        data = self.extract(xbrl_url, symbol)
        if data:
            doc = await NSEFinancials.find_one(
                NSEFinancials.symbol == symbol.upper(),
                NSEFinancials.date == data["date"],
                NSEFinancials.consolidated == x.get("consolidated", "Consolidated"),
            )
            if doc:
                doc.financials = data["financials"]
                await doc.save()
            else:
                await NSEFinancials(
                    symbol=symbol.upper(),
                    date=data["date"],
                    consolidated=x.get("consolidated", "Consolidated"),
                    financials=data["financials"],
                    broadcast_date=date_str,
                ).insert()

    async def download_announcements(self, symbol: str):
        # Implementation...
        pass

    async def download_shareholdings(self, symbol: str):
        # Implementation...
        pass

    # API wrappers
    def announcements_xbrls(self, symbol):
        return self._call(
            self.endpoints["announcements-equity"].format(symbol=symbol.upper())
        ).json()

    def annual_results_xbrls(self, symbol):
        return self._call(
            self.endpoints["integrated-filing"].format(symbol=symbol.upper())
        ).json()

    def quarterly_results_xbrls(self, symbol):
        return self._call(
            self.endpoints["quarterly-results"].format(symbol=symbol.upper())
        ).json()

    def integrated_filing_xbrls(self, symbol):
        return self._call(
            self.endpoints["integrated-filing"].format(symbol=symbol.upper())
        ).json()

    def shareholding_xbrls(self, symbol):
        return self._call(
            self.endpoints["shareholding-pattern"].format(symbol=symbol.upper())
        ).json()

    def annual_reports_xbrls(self, symbol):
        return self._call(
            self.endpoints["annual-reports"].format(symbol=symbol.upper())
        ).json()

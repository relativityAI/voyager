from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.exchange.nse import NSEIndia
from src.tools.news.marketsmithindia import MarketSmithIndia
from src.tools.web_screeners.screener import Screener
from src.tools.web_screeners.stockscans import StockScans
from src.tools.web_screeners.trendlyne import Trendlyne


async def fetch_screener_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch profile data for a stock from Screener.in"""
    logger.info(f"Screener fetch: {symbol}")
    scr = Screener()
    return scr.scrape(symbol)


def fetch_screener_screen(url: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch results from a custom screener URL."""
    logger.info(f"Screener screen fetch: {url}")
    scr = Screener()
    return scr.scrape_screen(url)


def fetch_trendlyne_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch data for a symbol from Trendlyne."""
    logger.info(f"Trendlyne fetch: {symbol}")
    tr = Trendlyne()
    return tr.fetch(symbol)


def fetch_stockscans_data(
    url: str, payload: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Fetch scan results from StockScans."""
    logger.info(f"Stockscans fetch: {url}")
    ss = StockScans()
    return ss.fetch_scan(url, payload)


def fetch_marketsmithindia_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch data for a symbol from MarketSmith India."""
    logger.info(f"MarketSmith India fetch: {symbol}")
    ms = MarketSmithIndia()
    return ms.fetch(symbol)


def fetch_nse_financials(symbol: str) -> List[Dict[str, Any]]:
    """Fetch and extract financial data (Integrated & Quarterly) from NSE."""
    logger.info(f"NSE financials fetch: {symbol}")
    nseindia = NSEIndia()
    results = []

    def _format_date(date_str):
        if not date_str:
            return None
        for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        return date_str

    # 1. Integrated Filings
    try:
        integrated_data = nseindia.integrated_filing_xbrls(symbol).get("data", [])
        for x in integrated_data:
            data = nseindia.process_xbrl(x, symbol, "integrated-filing")
            if data:
                data["xbrl"] = x.get("xbrl")
                data["broadcast_date"] = _format_date(x.get("broadcast_Date"))
                results.append(data)
    except Exception as e:
        logger.warning(f"Error processing integrated filings for {symbol}: {e}")

    # 2. Quarterly Results
    try:
        quarterly_data = nseindia.quarterly_results_xbrls(symbol)
        for x in quarterly_data:
            data = nseindia.process_xbrl(x, symbol, "quarterly-results")
            if data:
                data["xbrl"] = x.get("xbrl")
                data["broadcast_date"] = _format_date(x.get("broadCastDate"))
                results.append(data)
    except Exception as e:
        logger.warning(f"Error processing quarterly results for {symbol}: {e}")

    return results


def fetch_nse_announcements(symbol: str) -> List[Dict[str, Any]]:
    """Fetch announcements from NSE."""
    logger.info(f"NSE announcements fetch: {symbol}")
    nseindia = NSEIndia()
    try:
        return nseindia.announcements_xbrls(symbol)
    except Exception as e:
        logger.error(f"Error fetching announcements: {e}")
        return []


def fetch_nse_shareholdings(symbol: str) -> List[Dict[str, Any]]:
    """Fetch and extract shareholding patterns from NSE."""
    logger.info(f"NSE shareholdings fetch: {symbol}")
    nseindia = NSEIndia()
    results = []

    try:
        holdings = nseindia.shareholding_xbrls(symbol)
        for x in holdings:
            data = nseindia.process_xbrl(x, symbol, "shareholding-pattern")
            if data:
                data["xbrl"] = x.get("xbrl")
                data["broadcast_date"] = x.get("broadcastDate")
                results.append(data)
    except Exception as e:
        logger.error(f"Error fetching shareholdings: {e}")

    return results


def fetch_nse_annual_reports(symbol: str) -> List[Dict[str, Any]]:
    """Fetch annual report metadata from NSE."""
    logger.info(f"NSE annual reports list fetch: {symbol}")
    nseindia = NSEIndia()
    try:
        reports = nseindia.annual_reports_xbrls(symbol).get("data", [])
        for r in reports:
            r["symbol"] = symbol
        return reports
    except Exception as e:
        logger.error(f"Error fetching annual reports: {e}")
        return []


def extract_pdf_content(path_or_url: str) -> str:
    """Read content from a PDF path or URL."""
    from src.utils import read_pdf

    logger.info(f"Extracting PDF: {path_or_url}")
    return read_pdf(path_or_url)


def process_annual_report_toc(path_or_url: str) -> Dict[str, Any]:
    """Process an annual report to extract its Table of Contents."""
    from src.utils.annual_report_extraction import (
        extract_first_pages,
        extract_table_of_contents,
    )

    logger.info(f"Processing TOC for: {path_or_url}")
    num_pages, text = extract_first_pages(path_or_url)
    toc = extract_table_of_contents(text)
    return {"toc": toc, "num_pages": num_pages}

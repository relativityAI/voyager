from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.nse.client import NSEIndia


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

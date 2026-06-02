# news
from datetime import datetime

from loguru import logger

from src.utils.rate_limiter import RateLimitedSession, get_rate_limiter
from src.utils.web import generate_fake_headers


class MarketSmithIndia:
    """Scraper for MarketSmith India using their dynamic JSON APIs."""

    def __init__(self, calls_per_second: float = 10.0):
        """
        Initialize MarketSmith India scraper with rate limiting.

        Args:
            calls_per_second: Maximum API calls per second (default: 10)
        """
        self.base_url = "https://marketsmithindia.com"
        self.session = RateLimitedSession(calls_per_second, "marketsmithindia")
        self.rate_limiter = get_rate_limiter("marketsmithindia", calls_per_second)
        self.session.session.headers.update(generate_fake_headers())

    def fetch(self, symbol: str):
        """
        Fetches stock evaluation metrics from MarketSmith India.
        """
        symbol = symbol.upper()

        # Step 1: Initialize session and get MSSESSIONID cookie
        eval_url = f"{self.base_url}/mstool/eval/{symbol}/evaluation.jsp#/"
        response = self.session.get(eval_url)
        mssessionid = self.session.session.cookies.get("MSSESSIONID")

        if not mssessionid:
            logger.error("Failed to obtain MSSESSIONID from MarketSmith India.")
            return None

        # Step 2: Search for Instrument ID
        search_url = f"{self.base_url}/gateway/simple-api/ms-india/instr/srch.json"
        params = {"text": symbol, "lang": "en", "ver": "2", "ms-auth": mssessionid}

        search_resp = self.session.get(search_url, params=params)
        if search_resp.status_code != 200:
            logger.error(f"Search API failed for {symbol}")
            return None

        search_data = search_resp.json()
        results = search_data.get("response", {}).get("results", [])
        if not results:
            logger.error(f"No results found for symbol: {symbol}")
            return None

        instrument_id = results[0].get("instrumentId")
        actual_symbol = results[0].get("symbol")

        # Step 3: Fetch Symbol Details
        # Dates: today and 5 years ago (defaulting for general info)
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now().replace(year=datetime.now().year - 5)).strftime(
            "%Y%m%d"
        )

        details_url = f"{self.base_url}/gateway/simple-api/ms-india/instr/0/{instrument_id}/symboldetails.json"
        details_params = {
            "s": start_date,
            "e": end_date,
            "text": actual_symbol,
            "lang": "en",
            "isConsolidated": "0",
            "ms-auth": mssessionid,
        }

        details_resp = self.session.get(details_url, params=details_params)
        if details_resp.status_code != 200:
            logger.error(f"Symbol details API failed for {symbol}")
            return None

        details_data = details_resp.json().get("response", {})
        header = details_data.get("detailsGeneralInformationHeader", {})
        block = details_data.get("detailsGeneralInformationHeaderBlock", {})

        # Step 4: Fetch Wisdom (Industry Group Rank)
        wisdom_url = f"{self.base_url}/gateway/simple-api/ms-india/instr/0/{instrument_id}/wisdom.json"
        wisdom_params = {"lang": "en", "ver": "2", "x": "y", "ms-auth": mssessionid}

        wisdom_resp = self.session.get(wisdom_url, params=wisdom_params)
        group_rank = "N/A"
        if wisdom_resp.status_code == 200:
            wisdom_results = wisdom_resp.json().get("results", [])
            for item in wisdom_results:
                if item.get("name") == "Industry Group Rank":
                    group_rank = item.get("itemValue")
                    break

        # Map to Response structure
        result = {
            "symbol": actual_symbol,
            "Master Score": header.get("masterScore"),
            "EPS Rating": header.get("epsRank"),
            "Price Strength": header.get("rsNumericGrade"),
            "Acc/Dis Rating": header.get("accDisRating"),
            "Group Rank": group_rank,
            "EPS Growth Rate": f"{block.get('epsGrowthRate')}%"
            if block.get("epsGrowthRate") is not None
            else None,
            "Earnings Stability": block.get("earningsStability"),
            "P/E Ratio": header.get("pe"),
            "5-Year P/E Range": f"{block.get('pe5YearLow')}- {block.get('pe5YearHigh')}"
            if block.get("pe5YearLow") is not None
            else None,
            "Return on Equity": f"{header.get('roe')}%"
            if header.get("roe") is not None
            else None,
            "Cash Flow (INR)": round(block.get("cash_flow"), 2)
            if block.get("cash_flow") is not None
            else (
                round(block.get("cashFlow"), 2)
                if block.get("cashFlow") is not None
                else None
            ),
        }

        return result

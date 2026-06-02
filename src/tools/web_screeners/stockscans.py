# Web Screeners
import requests
from loguru import logger

from src.utils.rate_limiter import RateLimitedSession, get_rate_limiter
from src.utils.web import generate_fake_headers


class StockScans:
    def __init__(self, calls_per_second: float = 10.0):
        """
        Initialize StockScans with rate limiting.

        Args:
            calls_per_second: Maximum API calls per second (default: 10)
        """
        self.headers = generate_fake_headers()
        self.rate_limiter = get_rate_limiter("stockscans", calls_per_second)
        self.session = RateLimitedSession(calls_per_second, "stockscans")

    def fetch_scan(self, url: str, payload: dict) -> dict:
        """
        Fetches scan data from the API and returns the JSON response.
        """
        logger.info(f"Fetching scan data from {url}")
        try:
            # Use rate-limited session for the request
            response = self.session.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.success(f"Successfully fetched data from {url}")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return {}
        except ValueError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return {}

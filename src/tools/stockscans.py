import requests
from loguru import logger
from src.utils.web import generate_fake_headers

class StockScans:
    def __init__(self):
        self.headers = generate_fake_headers()

    def fetch_scan(self, url: str, payload: dict) -> dict:
        """
        Fetches scan data from the API and returns the JSON response.
        """
        logger.info(f"Fetching scan data from {url}")
        try:
            response = requests.post(url, headers=self.headers, json=payload)
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

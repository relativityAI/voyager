# Web Screeners
from urllib.parse import quote

from bs4 import BeautifulSoup

from src.utils.rate_limiter import RateLimitedSession, get_rate_limiter
from src.utils.web import generate_fake_headers


class Tijori(object):
    def __init__(self, calls_per_second: float = 10.0):
        """
        Initialize Tijori with rate limiting.

        Args:
            calls_per_second: Maximum API calls per second (default: 10)
        """
        self.search = (
            "https://www.tijorifinance.com/api/v1/ind/company_search/?q={share_name}"
        )
        self.company = "https://www.tijorifinance.com/company/"
        self.rate_limiter = get_rate_limiter("tijori", calls_per_second)
        self.session = RateLimitedSession(calls_per_second, "tijori")

    def fetch(self, share_name):
        search_url = self.search.format(share_name=quote(share_name))
        # Use rate-limited session for the request
        response = self.session.get(search_url, headers=generate_fake_headers()).json()
        slug = response[0]["slug"]

        company_url = self.company + slug
        # Use rate-limited session for the request
        response = self.session.get(company_url, headers=generate_fake_headers())

        if response.status_code != 200:
            raise Exception(f"Could not fetch : {company_url}")

        soup = BeautifulSoup(response.content, "html.parser")

        custom_ratios = soup.find_all("div", class_="custom_ratio")
        about = soup.find("div", class_="about")
        forensic = soup.find("section", id="forensic")
        marketshare = soup.find("section", id="marketshare")
        revenuemix = soup.find("section", id="revenuemix")
        competitors = soup.find("section", id="competitors")
        connections = soup.find("section", id="connections")  # customers
        discussions_and_analysis = soup.find("section", id="discussions_and_analysis")

        print(revenuemix)


if __name__ == "__main__":
    tijori = Tijori()
    tijori.fetch("kei industries")

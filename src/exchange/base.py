import requests
from io import BytesIO
from src.utils import console
from contextlib import nullcontext
from src.exchange.utils import (
    parse_xbrl, 
    get_fake_user_agent,
    get_headers
    )

class StockExchangeBase:
    def __init__(
            self,
            exchange : str = "nse",
            exchange_base_url: str = "https://www.nseindia.com",
            share_base_url_format :str = "https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
        ):

        self.exchange = exchange
        self._check_exchange()
        self.exchange_base_url = exchange_base_url
        self.share_base_url_format = share_base_url_format

        self.session = requests.Session()
        self.default_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        self.headers = {
                "User-Agent": self.default_user_agent ,
                "Accept": "application/json, text/plain, */*",
                "Connection": "keep-alive",
                "Referer": "https://www.nseindia.com/",
            }

    def _check_exchange(self):
        if self.exchange.lower() not in ['nse', 'bse']:
            raise Exception("Wrong exchange tag. Choose either nse or bse")

    def set_cookies(self, symbol, random_user_agent=True, timeout=10):
        if random_user_agent:   
            self.headers = get_headers(self.exchange)
        url = self.share_base_url_format.format(symbol=symbol)
        console.log(f"[yellow]Setting {self.exchange} cookies : {url}" )
        try:
            response = self.session.get(url, headers=self.headers, timeout=timeout)
            console.log(f"[green]Header response code : {response.status_code}")
        except Exception as e:
            console.log(f"[red]Failed to set Headers: {e}")
            response = None


    def api_call(self, url, symbol=None, max_call_attempts=3, show_code=True, timeout=10, display=False):
        for attempt in range(max_call_attempts):

            # 1 - set cookie if no cookie
            cookies = self.session.cookies.get_dict()
            if len(cookies) == 0:
                if display:
                    console.log("[yellow]Cookies not set. Setting initial cookies.")
                self.set_cookies(symbol)

            # 2 - send call
            if display and show_code:
                context = console.status("[bold green]Calling endpoint...[/bold green]", spinner="line")
            else:
                context = nullcontext()

            with context:
                try:
                    response = self.session.get(url, headers=self.headers, timeout=timeout)
                    if display:
                        console.log(f"[green]Call response code : {response.status_code}")
                except Exception as e:
                    if display:
                        console.log(f"[red]Request failed: {e}")
                    response = None

            # 3 - Check response
            if response and response.status_code == 200:
                return response
            else:
                if display:
                    console.log(f"[yellow]Attempt {attempt + 1} failed. Resetting cookies and retrying...")
                    self.set_cookies(symbol)

        if display:
            console.log("[red]Exceeded max API call attempts.")
        return None


    def fetch_xbrl(self, url, display=False):
        if display:
            console.log(f"📥 Downloading XBRL file from: {url}")

        r = self.api_call(url, show_code=False, display=False)

        if not r:
            return None

        if r.status_code == 200:
            if display:
                console.log(f"✅ Downloaded succesfully")
            return BytesIO(r.content)
        else:
            raise Exception(f"Failed to download file: {r.status_code}")

    # main data sources

    def announcements_xbrls(self):
        console.log("[bold]Implement announcement method here.")

    def annual_results_xbrls(self):
        pass

    def quarterly_results_xbrls(self):
        pass

    def shareholdings_xbrls(self):
        pass

    def annual_results_xbrls(self):
        pass

    def corporate_information(self):
        console.log("[bold]Implement corporate information method here.")
        pass

    def extract_pdf(self):
        pdf

    
    
    
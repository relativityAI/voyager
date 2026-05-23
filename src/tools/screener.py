from src.utils.web import generate_fake_headers
from bs4 import BeautifulSoup
import requests
import pandas as pd
import numpy as np
import re
from datetime import datetime
import calendar
from collections import defaultdict
from io import BytesIO
from loguru import logger

class Screener:
    def __init__(self):
        self.headers = generate_fake_headers()

    def _sanitize_data(self, data):
        """Recursively convert non-JSON compliant floats (NaN, Inf) to None."""
        if isinstance(data, dict):
            return {k: self._sanitize_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_data(v) for v in data]
        elif isinstance(data, float):
            if np.isnan(data) or np.isinf(data):
                return None
        return data

    def generate_url(self, symbol: str, consolidated=True):
        url = f"https://www.screener.in/company/{symbol.upper()}"
        if consolidated:
            url += "/consolidated/"
        return url

    def process_date(self, date: str):
        try:
            d = date.strip()
            if d.upper() == "TTM":
                return "TTM"

            parts = d.split()
            if len(parts) == 2 and parts[0].isdigit():
                day, month = parts
                try:
                    dt = datetime.strptime(f"{day} {month} {datetime.now().year}", "%d %b %Y")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass

            if len(parts) == 3 and parts[0].isdigit():
                day, month, year = parts
                try:
                    dt = datetime.strptime(f"{day} {month} {year}", "%d %b %Y")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass

            for i in range(len(parts) - 1):
                month, year = parts[i], parts[i + 1]
                try:
                    dt = datetime.strptime(f"{month} {year}", "%b %Y")
                    last_day = calendar.monthrange(dt.year, dt.month)[1]
                    return f"{dt.year:04d}-{dt.month:02d}-{last_day:02d}"
                except ValueError:
                    continue
            raise ValueError(f"Could not parse date from: {date}")
        except:
            return date

    def clean(self, s):
        return re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", s).strip()

    def scrape(self, symbol: str):
        url = self.generate_url(symbol)
        response = requests.get(url, headers=self.headers, timeout=10)
        dfs = pd.read_html(BytesIO(response.content))
        
        reference = {
            0: "quarterly-results", 1: "annual-results", 2: "sales-growth",
            3: "profit-growth", 4: "price-cagr", 5: "return-on-equity",
            6: "balance-sheet", 7: "cash-flow", 8: "ratios", 9: "new-1",
            10: "new-2", 11: "quarterly-shareholding", 12: "annual-shareholding",
        }

        items = defaultdict(dict)
        for _, df in enumerate(dfs):
            df = df.set_index(df.columns[0])
            df = df.replace(np.nan, None)
            cols = self.clean(" ".join(list(map(str, df.columns))).lower())
            rows = self.clean(" ".join(list(map(str, df.index))).lower())

            month_counts = sum(int(x in cols) for x in ["dec", "mar", "jun", "sep"])
            pnl_metrics_counts = sum(int(x in rows) for x in ["sales", "expenses", "profit", "eps"])
            bs_metrics_counts = sum(int(x in rows) for x in ["assets", "equity", "borrowings", "liabilities"])
            cf_metrics_counts = sum(int(x in rows) for x in ["activity", "cash", "cash flow", "operating"])
            ratios_metrics_counts = sum(int(x in rows) for x in ["roce", "debtor days", "conversion cycle", "working capital days"])
            holdings_count = sum(int(x in rows) for x in ["promoters", "fiis", "diis", "public"])

            category = None
            if month_counts >= 3 and pnl_metrics_counts >= 3: category = reference[0]
            elif "compounded sales growth" in cols: category = reference[2]
            elif "compounded profit growth" in cols: category = reference[3]
            elif "stock price cagr" in cols: category = reference[4]
            elif "return on equity" in cols: category = reference[5]
            elif month_counts <= 2:
                if pnl_metrics_counts >= 3: category = reference[1]
                elif bs_metrics_counts >= 3: category = reference[6]
                elif cf_metrics_counts >= 3: category = reference[7]
                elif ratios_metrics_counts >= 3: category = reference[8]
                elif holdings_count >= 3: category = reference[12]
            elif holdings_count >= 3:
                if month_counts >= 3: category = reference[11]

            if not category: continue

            if category in [reference[2], reference[3], reference[4], reference[5]]:
                data = df.to_dict()
                data = {x.replace(".1", ""): data[x] for x in data.keys()}
                items[category] = data
            else:
                for i, row in df.iterrows():
                    values = {self.process_date(date): val for date, val in row.to_dict().items()}
                    items[category][self.clean(str(i))] = values

        soup = BeautifulSoup(response.content, "html.parser")
        
        # Ratios
        ratios_div = soup.find("div", class_="company-ratios")
        if ratios_div:
            ratios_ul = ratios_div.find("ul", id="top-ratios")
            if ratios_ul:
                company_ratios = ratios_ul.find_all("li")
                items["ratios"] = {
                    x.find("span", class_="name").get_text().strip(): 
                    float(x.find("span", class_="value").find("span", class_="number").get_text().replace(",", ""))
                    for x in company_ratios
                }

        # About
        about_div = soup.find("div", class_="company-profile")
        if about_div:
            about_text = about_div.find("div", class_="about")
            if about_text:
                items["about"] = about_text.get_text().strip()

        # Annual Reports
        reports = []
        reports_div = soup.find("div", class_="annual-reports")
        if reports_div:
            for i in reports_div.find_all("a", href=True):
                reports.append({"year": i.get_text().split()[-1], "url": i["href"]})
        items["annual-report"] = reports

        # Credit Ratings
        ratings = []
        ratings_div = soup.find("div", class_="credit-ratings")
        if ratings_div:
            for i in ratings_div.find_all("a", href=True):
                try:
                    div_text = i.find("div").get_text()
                    if "from" in div_text:
                        date, org = div_text.split("from")
                        ratings.append({"organization": org.strip(), "date": self.process_date(date), "url": i["href"]})
                except:
                    pass
        items["credit-ratings"] = ratings

        return self._sanitize_data(items)

    def scrape_screen(self, base_url: str):
        """
        Scrapes multiple pages of a Screener 'screen' URL.
        """
        all_data = []
        page = 1
        
        # Strip existing page param if any
        if "page=" in base_url:
            import re
            base_url = re.sub(r'([&?])page=\d+', r'\1', base_url).rstrip('?&')

        while True:
            sep = "&" if "?" in base_url else "?"
            url = f"{base_url}{sep}page={page}"
            
            logger.info(f"Scraping Screen Page {page}: {url}")
            try:
                response = requests.get(url, headers=self.headers, timeout=10)
                response.raise_for_status()
                
                # Use BeautifulSoup to find the table first - more robust than pd.read_html alone
                soup = BeautifulSoup(response.text, 'html.parser')
                table_tags = soup.find_all('table')
                
                if not table_tags:
                    title = soup.find('title').text if soup.find('title') else "No title"
                    logger.error(f"No <table> tags found on page {page}. Page title: '{title}'. Length: {len(response.text)}")
                    if "Login" in title or "Sign in" in title:
                        logger.error("It seems Screener is redirecting to a login page. This screen might be private.")
                    break

                logger.info(f"Found {len(table_tags)} table tags on page {page}")
                
                data_df = None
                for table_tag in table_tags:
                    # Pass the HTML of the single table to pandas
                    # Wrap in StringIO to avoid warnings/errors
                    from io import StringIO
                    df_list = pd.read_html(StringIO(str(table_tag)))
                    if not df_list:
                        continue
                    df = df_list[0]
                    
                    cols = [str(c).lower() for c in df.columns]
                    if any(x in cols for x in ['name', 's.no.', 's.no']):
                        data_df = df
                        break
                
                if data_df is None or data_df.empty:
                    logger.warning(f"Could not identify the main data table among {len(table_tags)} total tables on page {page}.")
                    break


                
                if data_df is None or data_df.empty:
                    logger.info("No more data tables found. Stopping.")
                    break
                
                # Clean up DataFrame (remove NaN)
                data_df = data_df.replace(np.nan, None)
                all_data.extend(data_df.to_dict(orient="records"))
                
                # Check for pagination 'Next' link to decide if we continue
                soup = BeautifulSoup(response.content, 'html.parser')
                next_btn = soup.find('a', string=lambda t: t and 'Next' in t)
                if not next_btn:
                    logger.info("No 'Next' button found. End of results.")
                    break
                
                page += 1
                if page > 50: # Safety break
                    break
                    
            except Exception as e:
                logger.error(f"Error scraping screen page {page}: {e}")
                break
        
        return self._sanitize_data(all_data)


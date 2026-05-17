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
        company_ratios = soup.find("div", class_="company-ratios").find("ul", id="top-ratios").find_all("li")
        items["ratios"] = {
            x.find("span", class_="name").get_text().strip(): 
            float(x.find("span", class_="value").find("span", class_="number").get_text().replace(",", ""))
            for x in company_ratios
        }

        items["about"] = soup.find("div", class_="company-profile").find("div", class_="about").get_text()

        reports = []
        for i in soup.find("div", class_="annual-reports").find_all("a", href=True):
            reports.append({"year": i.get_text().split()[-1], "url": i["href"]})
        items["annual-report"] = reports

        ratings = []
        for i in soup.find("div", class_="credit-ratings").find_all("a", href=True):
            date, org = i.find("div").get_text().split("from")
            ratings.append({"organization": org, "date": self.process_date(date), "url": i["href"]})
        items["credit-ratings"] = ratings

        return self._sanitize_data(items)

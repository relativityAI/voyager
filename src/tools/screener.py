from src.utils.web import generate_fake_headers
from bs4 import BeautifulSoup
import requests

import pandas as pd
import numpy as np
from pprint import pprint
import re
from datetime import datetime
import calendar
from collections import defaultdict


def generate_screener_url(symbol: str, consolidated=True):
    url = f"https://www.screener.in/company/{symbol.upper()}"
    if consolidated:
        url += "/consolidated/"
    return url


def process_date(date: str):
    try:
        d = date.strip()

        if d.upper() == "TTM":
            return "TTM"

        parts = d.split()

        # Case 1: "DD Mon"  (no year)  → assume current year
        if len(parts) == 2 and parts[0].isdigit():
            day, month = parts
            try:
                dt = datetime.strptime(
                    f"{day} {month} {datetime.now().year}", "%d %b %Y"
                )
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Case 2: "DD Mon YYYY"
        if len(parts) == 3 and parts[0].isdigit():
            day, month, year = parts
            try:
                dt = datetime.strptime(f"{day} {month} {year}", "%d %b %Y")
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Case 3: fallback to month-year parsing (your original logic)
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


def clean(s):
    return re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", s).strip()


def screener_scrape_symbol(symbol):

    url = generate_screener_url(symbol)
    response = requests.get(url, headers=generate_fake_headers(), timeout=10)

    dfs = pd.read_html(response.content)
    n = len(dfs)

    reference = {
        # do not change the order
        0: "quarterly-results",
        1: "annual-results",
        2: "sales-growth",
        3: "profit-growth",
        4: "price-cagr",
        5: "return-on-equity",
        6: "balance-sheet",
        7: "cash-flow",
        8: "ratios",
        9: "new-1",
        10: "new-2",
        11: "quarterly-shareholding",
        12: "annual-shareholding",
    }

    items = defaultdict(dict)

    for _, df in enumerate(dfs):

        df = df.set_index(df.columns[0])
        df = df.replace(np.nan, None)

        cols = clean(" ".join(list(map(str, df.columns))).lower())
        rows = clean(" ".join(list(map(str, df.index))).lower())

        month_counts = sum(int(x in cols) for x in ["dec", "mar", "jun", "sep"])
        pnl_metrics_counts = sum(
            int(x in rows) for x in ["sales", "expenses", "profit", "eps"]
        )
        bs_metrics_counts = sum(
            int(x in rows) for x in ["assets", "equity", "borrowings", "liabilities"]
        )
        cf_metrics_counts = sum(
            int(x in rows) for x in ["activity", "cash", "cash flow", "operating"]
        )
        ratios_metrics_counts = sum(
            int(x in rows)
            for x in ["roce", "debtor days", "conversion cycle", "working capital days"]
        )

        holdings_count = sum(
            int(x in rows) for x in ["promoters", "fiis", "diis", "public"]
        )

        if month_counts >= 3 and pnl_metrics_counts >= 3:
            category = reference[0]

        elif "compounded sales growth" in cols:
            category = reference[2]

        elif "compounded profit growth" in cols:
            category = reference[3]

        elif "stock price cagr" in cols:
            category = reference[4]

        elif "return on equity" in cols:
            category = reference[5]

        elif month_counts <= 2:
            if pnl_metrics_counts >= 3:
                category = reference[1]

            elif bs_metrics_counts >= 3:
                category = reference[6]

            elif cf_metrics_counts >= 3:
                category = reference[7]

            elif ratios_metrics_counts >= 3:
                category = reference[8]

            elif holdings_count >= 3:
                category = reference[12]

        elif holdings_count >= 3:
            if month_counts >= 3:
                category = reference[11]

        if (
            category == reference[2]
            or category == reference[3]
            or category == reference[4]
            or category == reference[5]
        ):

            data = df.to_dict()
            data = {x.replace(".1", ""): data[x] for x in data.keys()}
            items[category] = data

        else:
            for i, row in df.iterrows():
                key = i
                values = {
                    process_date(date): val for date, val in row.to_dict().items()
                }
                key = clean(str(key))
                items[category][key] = values

    ###################################################

    soup = BeautifulSoup(response.content, "html.parser")

    company_ratios = (
        soup.find("div", class_="company-ratios")
        .find("ul", id="top-ratios")
        .find_all("li")
    )

    ratios = {
        x.find("span", class_="name")
        .get_text()
        .strip(): float(
            x.find("span", class_="value")
            .find("span", class_="number")
            .get_text()
            .replace(",", "")
        )
        for x in company_ratios
    }

    items["ratios"] = ratios

    about_company = (
        soup.find("div", class_="company-profile")
        .find("div", class_="about")
        .get_text()
    )

    items["about"] = about_company

    reports = []
    for i in soup.find("div", class_="annual-reports").find_all("a", href=True):
        year = i.get_text().split()[-1]
        url = i["href"]

        item = {"year": year, "url": url}
        reports.append(item)
    items["annual-report"] = reports

    ratings = []
    for i in soup.find("div", class_="credit-ratings").find_all("a", href=True):
        date, org = i.find("div").get_text().split("from")
        url = i["href"]

        item = {"organization": org, "date": process_date(date), "url": url}
        ratings.append(item)
    items["credit-ratings"] = ratings

    return items

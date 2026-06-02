# NSE Scrapers here

import pandas as pd
from rich.console import Console

from .exchanges.nse import NSEIndiaV2

# ===========================
nseindia = NSEIndiaV2()

console = Console()
# ===========================


class ScraperBase(object):
    """docstring for ScraperBase."""

    def __init__(
        self,
    ):
        super(ScraperBase, self).__init__()
        self.url_col = "url"
        self.symbols_table = "symbols"
        self.content_col = "content"

    def _rename_column(self, df: pd.DataFrame, old: str, new: str):
        return df.rename(columns={old: new})

    def _process_sources(
        self,
        df,
        raw_date_col,
        processed_date_col,
        raw_unique_key_col,
        category,
    ):
        df = self._rename_column(df, raw_date_col, processed_date_col)
        # df[processed_date_col] = pd.to_datetime(df[processed_date_col], format='%Y-%m-%d', errors='coerce')
        df = self._rename_column(df, raw_unique_key_col, self.url_col)
        df["category"] = category
        return df


class AnnouncementScraper(ScraperBase):
    """docstring for AnnouncementScraper.

    The kinda good thing here, is that we dont really need from_date & to_date while scraping.
    Only during reading.
    Because -
    Scraping data is not returned
    And DB manager upserts smartly updates the DB
    And since I'm seperating get and post calls in the API
    So
    Its chiller now."""

    def __init__(self):
        super(AnnouncementScraper, self).__init__()

        self.nseindia = nseindia

        self.table_name = "sources"
        self.filter_columns = [
            "desc",
            "attchmntFile",
            "attchmntText",
        ]  # columns to check for keyword like presentation
        self.raw_date_col = "sort_date"
        self.processed_date_col = "date"
        self.raw_unique_key_col = "attchmntFile"

        self.category = "announcements"

    def scrape(self, symbol):
        df = self.nseindia.announcements_xbrls(symbol)
        df = self._process_sources(
            df,
            raw_date_col=self.raw_date_col,
            processed_date_col=self.processed_date_col,
            raw_unique_key_col=self.raw_unique_key_col,
            category=self.category,
        )
        return df


class FinancialScraper(ScraperBase):
    """
    docstring for FinancialScraper.
    Scrapes only Financials XBRLS
    """

    def __init__(self):
        super(FinancialScraper, self).__init__()

        self.nseindia = nseindia

        self.table_name = "sources"
        self.raw_date_col = "toDate"
        self.processed_date_col = "date"
        self.raw_unique_key_col = "xbrl"

        self.html_result_col = "resultDetailedDataLink"

        self.category = "financials"

    def choose_url(self, row):
        a = row[self.raw_unique_key_col]
        b = row[self.html_result_col]

        if isinstance(a, str) and a.strip().lower().endswith(".xml"):
            return a
        else:
            return b

    def scrape(self, symbol):

        df = self.nseindia.financials_xbrls(symbol)

        # We need to perform an additional step here
        # Merge the xbrl col with the older .html web link cols, under the xbrl col together
        # Else its just a waste of data

        df[self.raw_unique_key_col] = df.apply(self.choose_url, axis=1)

        df = self._process_sources(
            df,
            raw_date_col=self.raw_date_col,
            processed_date_col=self.processed_date_col,
            raw_unique_key_col=self.raw_unique_key_col,
            category=self.category,
        )

        console.log(f"[blue]Retrieved {len(df)} financials XBRL sources.")
        return df


class ShareholdingScraper(ScraperBase):
    """docstring for ShareholdingScraper."""

    def __init__(self):
        super(ShareholdingScraper, self).__init__()

        self.nseindia = nseindia

        self.table_name = "sources"
        self.raw_date_col = "date"
        self.processed_date_col = "date"
        self.raw_unique_key_col = "xbrl"

        self.category = "shareholding"

    def scrape(self, symbol):

        df = self.nseindia.shareholding_xbrls(symbol)
        df = self._process_sources(
            df,
            raw_date_col=self.raw_date_col,
            processed_date_col=self.processed_date_col,
            raw_unique_key_col=self.raw_unique_key_col,
            category=self.category,
        )

        console.log(f"[blue]Retrieved {len(df)} Shareholding XBRL sources.")
        return df


class AnnualReportScraper(ScraperBase):
    """docstring for AnnualReportScraper."""

    def __init__(self):
        super(AnnualReportScraper, self).__init__()

        self.nseindia = nseindia

        self.table_name = "sources"
        self.raw_date_col = "toYr"
        self.raw_unique_key_col = "fileName"
        self.processed_date_col = "date"
        self.content_col = "content"

        self.category = "annual_report"

    def scrape(self, symbol):
        df = self.nseindia.annual_reports_xbrls(symbol)
        df = self._process_sources(
            df,
            raw_date_col=self.raw_date_col,
            processed_date_col=self.processed_date_col,
            raw_unique_key_col=self.raw_unique_key_col,
            category=self.category,
        )
        return df

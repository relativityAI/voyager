import json
from typing import List, Optional, Union

import pandas as pd
from bson import json_util
from src.exchange.nse import NSEIndia

from src.data.utils import extract_text_from_pdf, get_exchange_from_url
from src.exchange.bse import BSEIndia
from src.exchange.utils import parse_xbrl
from src.pipelines import fundamentals_pipeline, valuations_pipeline
from src.utils import MongoDBHandler, console, get_list_size, track


def sanitize_mongo_response(data):
    return json.loads(json_util.dumps(data))


class DataManager(object):
    """
    This should be an easy to use class with a hell lotta abstraction on reading vs scraping logic.
    """

    def __init__(self):
        self.db = MongoDBHandler()
        self.nseindia = NSEIndia()
        self.bseindia = BSEIndia()

    def extract(self, url: str):

        exchange = get_exchange_from_url(url)
        if not exchange:
            console.log("[red]Could not extract exchange from doc url.")

        if exchange == "nse":
            response = self.nseindia.api_call(url=url, show_code=False)
        elif exchange == "bse":
            response = self.bseindia.api_call(url=url, show_code=False)

        text = extract_text_from_pdf(response)
        return text

    # READING
    # pdf docs

    def read_announcements(self, symbol, from_date=None, to_date=None):
        collection = self.db.get_collection("announcements")

        query = {"symbol": symbol}
        if from_date or to_date:
            query["sort_date"] = {}
            if from_date:
                query["sort_date"]["$gte"] = from_date
            if to_date:
                query["sort_date"]["$lte"] = to_date

        data = self.db.read(collection, query)
        return sanitize_mongo_response(data)

    def read_annual_reports(self, from_date=None, to_date=None):
        collection = self.db.get_collection("annual_reports")

        from_year = from_date.split("-")[0]
        to_year = to_date.split("-")[0]

        query = {"symbol": symbol}
        if from_date or to_date:
            query["sort_date"] = {}
            if from_date:
                query["toYr"]["$gte"] = from_year
            if to_date:
                query["toYr"]["$lte"] = to_year

        data = self.db.read(collection, query)
        return sanitize_mongo_response(data)

    # Numbers and financials
    def read_results(
        self,
        symbol,
        period="annual",
        filter_keys: Optional[List[str]] = [],
        from_date=None,
        to_date=None,
        collection_name="results",
        filtered=False,
    ):
        period_context_ref_maps = {
            "quarterly": ["OneD", "OneI"],
            "annual": ["FourD", "OneI"],
        }

        period = period.lower()
        if period not in period_context_ref_maps.keys():
            return {"error": "Wrong period, choose quarterly or annual."}

        context_refs = period_context_ref_maps[period]

        collection = self.db.get_collection(collection_name)

        query = {"symbol": symbol}
        if from_date or to_date:
            query["toDate"] = {}
            if from_date:
                query["toDate"]["$gte"] = from_date
            if to_date:
                query["toDate"]["$lte"] = to_date

        # Context ref filter
        if context_refs:
            query["contextRef"] = {"$in": context_refs}

        # Tag filter
        if filter_keys:
            query["tag"] = {"$in": filter_keys}
            # or if you want substring match:
            # query["tag"] = {"$regex": filter_keyword, "$options": "i"}

        projection = None
        if filtered:
            projection = {
                "_id": 0,
                # "audited" : 1,
                "symbol": 1,
                "tag": 1,
                "value": 1,
                # "companyName" : 1,
                "consolidated": 1,
                "contextRef": 1,
                # "cumulative" : 1,
                # "filingDate" : 1,
                # "financialYear" : 1,
                # "fromDate" : 1,
                "toDate": 1,
                # "isin" : 1,
                # "period" : 1,
                # "relatingTo" : 1,
                "xbrl": 1,
            }

        data = self.db.read(collection, query, projection)
        return sanitize_mongo_response(data)

    def read_shareholdings(
        self,
        symbol,
        filter_keys: Optional[List[str]] = [],
        from_date=None,
        to_date=None,
        collection_name="shareholdings",
    ):
        collection = self.db.get_collection(collection_name)
        console.log(symbol)
        query = {"symbol": symbol}
        if from_date or to_date:
            query["date"] = {}
            if from_date:
                query["date"]["$gte"] = from_date
            if to_date:
                query["date"]["$lte"] = to_date
        # Tag filter
        if filter_keys:
            query["tag"] = {"$in": filter_keys}
            # or if you want substring match:
            # query["tag"] = {"$regex": filter_keyword, "$options": "i"}

        print(query)
        data = self.db.read(collection, query)
        return sanitize_mongo_response(data)

        pass

    def read_fundamentals(
        self,
        symbol,
        ratios: Union[str, List[str]] = ["pat_margin"],
        from_date=None,
        to_date=None,
        period="quarterly",
        exchange="NSE",
        collection_name="fundamentals",
    ):
        collection = self.db.get_collection(collection_name)

        if isinstance(ratios, List):
            projection = {x: 1 for x in ratios}
        else:
            projection = {ratios: 1}

        projection["_id"] = 0  # dont want id
        projection["date"] = 1

        query = {"symbol": symbol, "period": period, "exchange": exchange}
        if from_date or to_date:
            query["date"] = {}
            if from_date:
                query["date"]["$gte"] = from_date
            if to_date:
                query["date"]["$lte"] = to_date

        data = self.db.read(collection=collection, query=query, projection=projection)
        return sanitize_mongo_response(data)

    def read_valuations(
        self,
        symbol,
        ratios: Union[str, List[str]] = ["price_to_earnings"],
        from_date=None,
        to_date=None,
        period="quarterly",
        exchange="NSE",
        collection_name="valuations",
    ):
        collection = self.db.get_collection(collection_name)

        if isinstance(ratios, List):
            projection = {x: 1 for x in ratios}
        else:
            projection = {ratios: 1}

        projection["_id"] = 0  # dont want id
        projection["date"] = 1

        query = {"symbol": symbol, "period": period, "exchange": exchange}
        if from_date or to_date:
            query["date"] = {}
            if from_date:
                query["date"]["$gte"] = from_date
            if to_date:
                query["date"]["$lte"] = to_date

        data = self.db.read(collection=collection, query=query, projection=projection)
        return sanitize_mongo_response(data)

    # SCRAPING + WRITING
    # pdf docs
    def download_announcements(self, symbol, unique_index="attchmntFile"):
        announcements = self.nseindia.announcements_xbrls(symbol)
        collection = self.db.get_collection("announcements")
        collection.create_index(unique_index, unique=True)
        insert_id = self.db.create(
            collection, documents=announcements, unique_cols=[unique_index]
        )
        # console.log(insert_id)
        return insert_id

    def download_annual_reports(self, symbol, unique_index="attchmntFile"):
        ann_reps = self.nseindia.annual_reports_xbrls(symbol)
        collection = self.db.get_collection("annual_reports")
        collection.create_index(unique_index, unique=True)
        insert_id = self.db.create(
            collection, documents=ann_reps, unique_cols=[unique_index]
        )
        # console.log(insert_id)
        return insert_id

    def download_symbols(self, exchange, country):

        # if country.upper().strip() == 'IN':

        # else:
        #     return None

        pass

    # Numbers and financials
    def download_results(
        self,
        symbol,
        collection_name="results",
        cols_to_keep=[
            "broadCastDate",
            "companyName",
            "consolidated",
            "contextRef",
            "filingDate",
            "fromDate",
            "period",
            "symbol",
            "tag",
            "toDate",
            "value",
            "xbrl",
        ],
        unique_cols=["symbol", "xbrl", "period", "toDate"],
        ignore_urls: List[str] = ["https://nsearchives.nseindia.com/corporate/xbrl/-"],
    ):
        collection = self.db.get_collection(collection_name)

        q_r_xbrls = self.nseindia.quarterly_results_xbrls(symbol)
        q_r_df = pd.DataFrame(q_r_xbrls)

        i_f_xbrls = self.nseindia.integrated_filing_xbrls(symbol)
        i_f_df = pd.DataFrame(i_f_xbrls["data"])
        i_f_df = i_f_df.rename(columns={"seq_Id": "seqNumber", "qe_Date": "toDate"})

        df = pd.concat([q_r_df, i_f_df], ignore_index=True)

        # processing dates
        df["toDate"] = pd.to_datetime(
            df["toDate"], format="%d-%b-%Y", errors="coerce", dayfirst=True
        )
        df["toDate"] = df["toDate"].dt.strftime("%Y-%m-%d")
        df = df.sort_values(by="toDate", ascending=False)

        df = df.drop(columns=[col for xol in cols_to_keep if col in df.columns])

        xbrls_dict = df.to_dict(orient="records")

        data_list = []
        for item in track(
            xbrls_dict,
            description="Downloading financials [xbrls & htmls] ...",
            total=len(xbrls_dict),
        ):
            url = item["xbrl"]

            if url in ignore_urls:
                continue

            # Check if it exists in the collection
            if collection.count_documents({"xbrl": url}, limit=1) != 0:
                break  # subsequent data must also exist

            resp = self.nseindia.fetch_xbrl(url, display=False)
            if not resp:
                continue
            data = parse_xbrl(resp, get_json=True)

            for record in data:
                data_item = item | record

                # data_item.pop("audited", None)
                # data_item.pop("companyName", None)
                # data_item.pop("cumulative", None)
                # data_item.pop("filingDate", None)
                # data_item.pop("financialYear", None)
                # data_item.pop("fromDate", None)
                # data_item.pop("isin", None)
                # data_item.pop("period", None)
                # data_item.pop("relatingTo", None)

                data_list.append(data_item)
        size = get_list_size(data_list)
        console.log(
            f"Data size: {size['b']} b, {size['kb']:.2f} kb, {size['mb']:.2f} mb"
        )
        with console.status(f"Inserting {len(data_list)}  items to DB...") as status:
            insert_ids = self.db.create(
                collection, documents=data_list, unique_cols=unique_cols
            )

        # console.log(insert_ids)
        # console.log(f"Successfully inserted {len(insert_ids)} financials to DB.")

    def download_shareholdings(self, symbol, collection_name="shareholdings"):
        collection = self.db.get_collection(collection_name)

        s_xbrls = self.nseindia.shareholding_xbrls(symbol)
        df = pd.DataFrame(s_xbrls)

        df["date"] = pd.to_datetime(
            df["date"], format="%d-%b-%Y", errors="coerce", dayfirst=True
        )
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        df = df.sort_values(by="date", ascending=False)

        xbrls_dict = df.to_dict(orient="records")

        data_list = []
        for item in track(
            xbrls_dict,
            description="Downloading shareholdings ...",
            total=len(xbrls_dict),
        ):
            url = item["xbrl"]

            if url == "https://nsearchives.nseindia.com/corporate/xbrl/-":
                continue

            # Check if it exists in the collection
            if collection.count_documents({"xbrl": url}, limit=1) != 0:
                break  # subsequent data must also exist

            resp = self.nseindia.fetch_xbrl(url)
            if not resp:
                continue
            data = parse_xbrl(resp, get_json=True)

            for record in data:
                data_item = item | record
                data_list.append(data_item)

        with console.status(f"Inserting {len(data_list)}  items to DB...") as status:
            insert_ids = self.db.create(collection, documents=data_list)

        console.log(insert_ids)

    def calculate_fundamentals(
        self,
        symbols: List[str],
        period="quarterly",
        collection_name="fundamentals",
    ):
        collection = self.db.get_collection(collection_name)

        result = fundamentals_pipeline(symbols, period)
        for sym in result:
            data = result[sym]
            with console.status(f"Inserting {len(data)} items to DB...") as status:
                insert_ids = self.db.create(
                    collection,
                    documents=data,
                    unique_cols=["symbol", "date", "exchange", "period"],
                )

        if insert_ids:
            return True
        return False

    def calculate_valuations(
        self,
        symbols: List[str],
        period="quarterly",
        collection_name="valuations",
    ):
        collection = self.db.get_collection(collection_name)

        result = valuations_pipeline(symbols=symbols, period=period)
        for sym in result:
            data = result[sym]
            with console.status(f"Inserting {len(data)} items to DB...") as status:
                insert_ids = self.db.create(
                    collection,
                    documents=data,
                    unique_cols=["symbol", "date", "exchange", "period"],
                )

        if insert_ids:
            return True
        return False

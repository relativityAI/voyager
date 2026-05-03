import typer
from pprint import pprint
from src.tools.nse import NSEIndia
from src.utils import (
    read_pdf,
    write_json
    )
from src.utils.mongodb import DB
from src.db import Data
from pprint import pprint
from datetime import datetime
import pandas as pd

import logging


##############################################


app = typer.Typer()

db = DB()


##############################################


from src.tools.valuepickr import search_forum, scrape_thread
from src.tools.screener import screener_scrape_symbol
from src.tools.trendlyne import Trendlyne
from src.tools.tijori import Tijori
from src.tools.ndtv import scrape_news


web_app = typer.Typer()
screener_app = typer.Typer()
trendlyne_app = typer.Typer()
valuepickr_app = typer.Typer()
tijori_app = typer.Typer()
morningstar_app = typer.Typer()


web_app.add_typer(screener_app, name="screener")
web_app.add_typer(trendlyne_app, name="trendlyne")
web_app.add_typer(tijori_app, name="tijori")
morningstar_app.add_typer(morningstar_app, name="morningstar")
valuepickr_app.add_typer(valuepickr_app, name="valuepickr")


app.add_typer(web_app, name="web")


@screener_app.command("share")
async def web_screener_share(symbol: str):
    logging.info(f"Screener scrape : {symbol}")
    logging.info("Scraping...")

    response = screener_scrape_symbol(symbol)
    if response:
        data = Data(
            source=  "screener",
            category="share",
            data=response
        )
        await data.insert()

    # return Exception("Failed to insert")
    print("ok")

@trendlyne_app.command("share")
def web_trendlyne_share(symbol: str, display=True):
    logging.info(f"Trendlyne scrape : {symbol}")
    logging.info("Scraping...")

    tr = Trendlyne()
    data = tr.fetch(symbol)
    if display:
        print(tr.format_output(data))
    return data


@trendlyne_app.command("industry")
def web_trendlyne_industries():
    pass


@trendlyne_app.command("sector")
def trendlyne_sectors_download():
    pass


@tijori_app.command("share")
def tijori_download(share_name: str, display=True):
    logging.info(f"Trendlyne scrape : {share_name}")
    logging.info("Scraping...")

    tr = Tijori()
    data = tr.fetch(share_name)
    if display:
        pprint(data)
    return data


@morningstar_app.command()
def morningstar_download():
    pass


@valuepickr_app.command()
def valuepickr_search(query: str):

    collection = db.get_collection("valuepickr-topics")
    db.create_index(collection, ["id", "slug"])

    topics = search_forum(query)
    for topic in topics:
        db.insert(collection, topic)

    logging.info("Scrape and save complete")


@valuepickr_app.command()
def valuepickr_thread_download():
    scrape_thread()


##############################################
nse_app = typer.Typer()

app.add_typer(nse_app, name="nse")


@nse_app.command("financials")
def nse_financials_download(symbol: str):
    nseindia = NSEIndia()

    collection = db.get_collection("nse-financials")
    db.create_index(collection, ["xbrl"])
    # db.create_index(collection, ['symbol', 'date', 'consolidated'])
    # db.create_index(collection, ['tag', 'symbol', 'date', 'contextRef', 'value'])

    for x in nseindia.integrated_filing_xbrls(symbol)["data"]:
        xbrl = x["xbrl"]
        broadcast_date = None
        if x["broadcast_Date"]:
            broadcast_date = datetime.strptime(
                x["broadcast_Date"], "%d-%b-%Y %H:%M:%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
        data = nseindia.extract(xbrl, symbol)
        if data:
            data["consolidated"] = x["consolidated"]
            data["xbrl"] = xbrl
            data["broadcast_date"] = broadcast_date

            db.insert(collection, data)

    for x in nseindia.quarterly_results_xbrls(symbol):
        xbrl = x["xbrl"]
        broadcast_date = None
        if x["broadCastDate"]:
            broadcast_date = datetime.strptime(
                x["broadCastDate"], "%d-%b-%Y %H:%M:%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
        data = nseindia.extract(xbrl, symbol)
        if data:
            data["consolidated"] = x["consolidated"]
            data["xbrl"] = xbrl
            data["broadcast_date"] = broadcast_date

            db.insert(collection, data)

    logging.info("Scrape and save complete")


@nse_app.command("announcements")
def nse_announcements_download(symbol: str):
    nseindia = NSEIndia()

    collection = db.get_collection("nse-announcements")
    db.create_index(collection, ["attchmntFile"])

    for x in nseindia.announcements_xbrls(symbol):
        db.insert(collection, x)

    logging.info("Scrape and save complete")


@nse_app.command("announcements-search")
def nse_announcements_search(
    symbol: str, keywords: str = "transcript", cutoff_date: str = "2026-01-01"
):
    import re

    collection = db.get_collection("nse-announcements")
    docs = collection.find(
        {
            "symbol": symbol,
            "attchmntText": {"$regex": re.compile(keywords, re.IGNORECASE)},
            "sort_date": {"$lte": cutoff_date},
        }
    )

    df = pd.DataFrame(docs)
    # print(df)

    return df.to_dict("records")


@nse_app.command("announcements-extract")
def nse_announcement_extract(path_or_url: str):
    collection = db.get_collection("nse-announcements")
    query = {}
    query["attchmntFile"] = path_or_url
    data = db.read(collection, query)

    if len(data) == 0:
        logging.error(
            "No document found in DB - Please scrape annual report sources first"
        )
        return

    text = read_pdf(path_or_url)
    return text


@nse_app.command("list-annual-reports")
def nse_annual_reports_list(symbol: str):
    collection = db.get_collection("nse-annual-reports")
    docs = collection.find({"symbol": symbol})

    df = pd.DataFrame(docs)

    print(df)

    return df.to_dict("records")


@nse_app.command("annual-reports")
def nse_annual_reports_download(symbol: str):
    nseindia = NSEIndia()

    collection = db.get_collection("nse-annual-reports")
    db.create_index(collection, ["fileName"])

    for x in nseindia.annual_reports_xbrls(symbol)["data"]:
        x["symbol"] = symbol
        db.insert(collection, x)

    logging.info("Scrape and save complete")


@nse_app.command("shareholdings")
def nse_shareholdings_download(symbol: str):
    nseindia = NSEIndia()

    # pprint(nseindia.shareholding_xbrls(symbol))

    collection = db.get_collection("nse-shareholdings")
    db.create_index(collection, ["xbrl"])

    for x in nseindia.shareholding_xbrls(symbol):
        xbrl = x["xbrl"]
        broadcast_date = None
        if x["broadcastDate"]:
            broadcast_date = datetime.strptime(
                x["broadcastDate"], "%d-%b-%Y %H:%M:%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
        data = nseindia.extract(xbrl, symbol)
        if data:
            # data['consolidated'] = x['consolidated']
            data["xbrl"] = xbrl
            data["broadcast_date"] = broadcast_date

            db.insert(collection, data)

    logging.info("Scrape and save complete")


@nse_app.command("process-annual-report")
def nse_process_annual_report(path_or_url: str):
    from src.utils.annual_report_extraction import (
        extract_first_pages,
        extract_table_of_contents,
    )

    logging.info("Confirming existence in DB...")
    collection = db.get_collection("nse-annual-reports")
    query = {}
    query["fileName"] = path_or_url
    data = db.read(collection, query)

    if len(data) == 0:
        logging.error(
            "No document found in DB - Please scrape annual report sources first"
        )
        return
    elif len(data) > 1:
        logging.error("Duplicate documents in DB - Please Fix")
        return

    data = data[0]

    if not "toc" in data.keys() or data["toc"] == None or data["toc"] == {}:
        num_pages, text = extract_first_pages(path_or_url)
        toc = extract_table_of_contents(text)

        collection.update_one(
            {"fileName": path_or_url}, {"$set": {"toc": toc, "num_pages": num_pages}}
        )
        logging.info(
            "Succesfully extracted and saved Table of Contents for the annual Report"
        )
    else:
        logging.info("Table of contents exists")


@nse_app.command("list-annual-report-section")
def nse_list_annual_report_sections(path_or_url: str):
    logging.info("Confirming existence in DB...")
    collection = db.get_collection("nse-annual-reports")
    query = {}
    query["fileName"] = path_or_url
    data = db.read(collection, query)

    if len(data) == 0:
        logging.error(
            "No document found in DB - Please scrape annual report sources first"
        )
        return
    elif len(data) > 1:
        logging.error("Duplicate documents in DB - Please Fix")
        return

    data = data[0]

    if not "toc" in data.keys() or data["toc"] == None or data["toc"] == {}:
        return "Table of contents / Sections - DO NOT EXIST for this annual report."
    else:
        return data["toc"]


@nse_app.command("download-annual-report-section")
def nse_annual_report_section_download(
    path_or_url: str,
    keywords: str = "management discussion analysis",
    lag: int = 0,
    # keywords : str = "governance"
):
    collection = db.get_collection("nse-annual-reports")
    query = {}
    query["fileName"] = path_or_url
    data = db.read(collection, query)

    if len(data) == 0:
        logging.error(
            "No document found in DB - Please scrape annual report sources first"
        )
        return

    data = data[0]

    if not "toc" in data.keys() or data["toc"] == None or data["toc"] == {}:
        logging.error(
            "No table of contents found, please use nse_process_annual_report first."
        )

    toc = data["toc"]
    num_pages = data["num_pages"]

    df = pd.DataFrame(toc)
    df = df.sort_values(by="page", ascending=True)

    def find_section(df, query, cutoff=0.6):

        import difflib

        sections = df["section"].str.lower().tolist()
        matches = difflib.get_close_matches(query.lower(), sections, n=1, cutoff=cutoff)

        if not matches:
            return None

        matched = matches[0]
        section = df[df["section"].str.lower() == matched]

        start, end = None, None
        toc_size = df.shape[0]
        start_index = int(section.index.item())
        start = df.loc[start_index, "page"].item()
        end_index = None

        if start_index + 1 == toc_size:
            end = num_pages
        elif start_index + 1 < toc_size:
            end = df[df.index == start_index + 1]["page"].item()

        return {"section": section["section"].item(), "start": start, "end": end}

    section = find_section(df, keywords)

    lag = int(lag)  # coz real page number might different than one on the toc

    content = read_pdf(path_or_url, section["start"] + 1, section["end"] + lag)
    # print(content)

    # print(section)

    return content


@nse_app.command("list-symbols")
def nse_distinct_symbols_financials():
    collection = db.get_collection("nse-financials")
    pprint(collection.distinct("symbol"))


@nse_app.command("full-download")
def nse_full_download(symbol: str):

    logging.info("Downloading financials")
    nse_financials_download(symbol)

    logging.info("Downloading Announcements")
    nse_announcements_download(symbol)

    logging.info("Downloading Shareholdings")
    nse_shareholdings_download(symbol)

    logging.info("Downloading Annual Reports")
    nse_annual_reports_download(symbol)

    available_annual_reports = nse_annual_reports_list(symbol)
    latest_report = available_annual_reports[0]["fileName"]
    logging.info("Extracting Latest Annual Report ToC")
    nse_process_annual_report(latest_report)

    logging.info("Full Data scrape and download complete")


##############################################


@app.command()
def stock_data_report(symbol: str, date: str, quarterly=True, consolidated=True):

    logging.info(f"Generating data report for {symbol} dated {date}\n")

    # Fetch and preprocess

    consolidated = "Consolidated" if consolidated else "Non-Consolidated"
    collection = db.get_collection("nse-financials")
    data = db.read(collection, {"symbol": "KEI"})
    dfs = [
        pd.DataFrame(x["financials"]).rename(columns={"value": x["broadcast_date"]})
        for x in data
        if x["consolidated"] == consolidated
    ]
    dfs = [
        d[
            (d["contextRef"] != "FourD") if quarterly else (d["contextRef"] == "FourD")
        ].drop(["contextRef"], axis=1)
        for d in dfs
    ]
    dfs = [d.set_index("tag") for d in dfs]
    dfs = [d[~d.index.duplicated(keep="first")] for d in dfs]
    df = pd.concat(dfs, axis=1).T
    df = df.apply(pd.to_numeric, errors="coerce")

    # Ratios calculation

    ratios = pd.DataFrame(index=df.index)

    # Profitability

    ratios["pat_margin"] = df["ProfitLossForPeriod"] / df["Income"]
    ratios["ebitda"] = (df["RevenueFromOperations"] - df["OtherIncome"]) - (
        df["Expenses"]
        - df["FinanceCosts"]
        - df["DepreciationDepletionAndAmortisationExpense"]
    )
    ratios["ebitda_margin"] = ratios["ebitda"] / df["RevenueFromOperations"]

    # ratios['op'] = df['ProfitBeforeTax'] + df['FinanceCosts']- df['OtherIncome'] - df["ExceptionalItemsBeforeTax"]
    # ratios['op'] = df['CostOfMaterialsConsumed'] + df['EmployeeBenefitExpense'] - df['']

    # Leverage

    ratios["debt"] = df["BorrowingsCurrent"] + df["BorrowingsNoncurrent"]
    ratios["current_assets"] = df["CurrentAssets"] + df["OtherCurrentAssets"]
    ratios["non_current_assets"] = df["NoncurrentAssets"] + df["OtherNoncurrentAssets"]
    # ratios['assets'] = ratios['current_assets'] + ratios['non_current_assets']
    ratios["assets"] = df["Assets"]
    ratios["current_liabilities"] = (
        df["CurrentLiabilities"] + df["OtherCurrentLiabilities"]
    )
    ratios["non_current_liabilities"] = (
        df["NoncurrentLiabilities"] + df["OtherNoncurrentLiabilities"]
    )
    # ratios['liabilities'] = ratios['current_liabilities'] + ratios['non_current_liabilities']
    ratios["liabilities"] = df["Liabilities"]
    ratios["current_ratio"] = ratios["current_assets"] / ratios["current_liabilities"]
    ratios["capital_employed"] = ratios["assets"] - ratios["current_liabilities"]
    ratios["roce"] = ratios["ebitda"] / ratios["capital_employed"]
    ratios["roa"] = df["ProfitLossForPeriod"] / ratios["assets"]

    pprint(ratios)

    report = f"""
    
    FINANCIALS - VALUATIONS

    price : 
    pe : 
    pb : 
    peg : 
    price/sales :     
    price/fcf : 
    ev/ebitda : 
    ev/ebit : 
    ev/sales : 
    ev/fcf : 
    dividend yield : 

    FINANCIALS - GROWTH

    revenue qoq : 
    revenue yoy : 
    profit qoq : 
    profit yoy : 
    revenue cagr 1y : 
    revenue cagr 3y : 
    revenue cagr 5y : 
    revenue cagr 10 : 
    profit cagr 1y : 
    profit cagr 3y : 
    profit cagr 5y : 
    profit cagr 10 : 
    ebitda cagr 1y : 
    ebitda cagr 3y : 
    ebitda cagr 5y : 
    ebitda cagr 10 : 
    cfo cagr :
    asset cagr : 

    FINANCIALS - PROFITABILITY
    
    roe :
    roce :
    roce : 
    roa : 
    opm : 
    pat margin : 
    ebitda margin : 
    gross margin : 
    margin expansion : 
    cfo/profit : 
    asset turnover : 

    FINANCIALS - PROFITABILITY

    d2e : 
    d2a : 
    financial leverage ratio : 
    interest coverage ratio: 
    current ratio : 
    quick ratio : 


    QUALITATIVE - INVESTOR PRESENTATIONS




    QUALITATIVE - CONCALL TRANSCRIPTS




    QUALITATIVE - NEWS




    QUALITATIVE - VALUEPICKR DISCUSSION FORUM




    QUALITATIVE - ANALYST REPORTS




    """

    # logging.info(report)


"""

@app.command()
def ndtv_news_download(
    from_year:int = 2025,
    from_mon:int = 10,
    to_year:int = 2025,
    to_mon:int = 11,
):

    news = scrape_news(
        from_year,
        from_mon,
        to_year,
        to_mon
    )

    print(news[:20])


@app.command()
def sec_10k_download(symbol: str):
    pass

@app.command()
def sec_10q_download(symbol: str):
    pass

@app.command()
def sec_8k_download(symbol: str):
    pass


@app.command()
def yt_search(query: str, max_results=20):
    urls  = youtube_search_results(query, max_results=int(max_results))

    results = []
    for url in urls:
        results.append(f"{url['publish_time']} - {url['title']} - {url['url_suffix']}")

    print("\n".join(results))

@app.command()
def yt_transcripts(id_or_url: str):
    tr = fetch_transcripts(id_or_url)
    print(parse_transcripts(tr))


"""

if __name__ == "__main__":
    app()

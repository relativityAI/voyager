from fastapi import FastAPI
import uvicorn
from bson import json_util
from src.utils.mongodb import DB 
import json 


from cli import (
    nse_announcements_download,
    nse_announcements_search,
    nse_annual_report_section_download,
    nse_annual_reports_list,
    nse_list_annual_report_sections,
    nse_announcement_extract,

    screener_download,
    trendlyne_download
    )


app = FastAPI(title="Voyager", version="1.0.0")
db = DB()

nse_financials = db.get_collection("nse-financials")
nse_shareholdings = db.get_collection("nse-shareholdings")


@app.get("/screener")
def screener_endpoint(symbol: str):
    data = screener_download(symbol=symbol, display=False)
    return data

@app.get("/trendlyne")
def trendlyne_endpoint(symbol: str):
    data = trendlyne_download(symbol=symbol, display=False)
    return data

@app.get("/nse-financials")
def nse_financials_endpoint(
    symbol:str, 
    start:str = None, 
    end:str = None, 
    consolidated=True
    ):

    filters = {
        "symbol" : symbol, 
        "consolidated" : "Consolidated" if consolidated else "Non-Consolidated" 
        }

    if start:
        filters['date'] = {
            "$gte" : start,
        }
    if end:
        filters['date'] = {
            "$lte" : end
        }

    data = json_util.dumps(db.read(nse_financials, filters))
    return json.loads(data)


@app.get("/nse-shareholdings")
def nse_shareholdings_endpoint(
    symbol:str, 
    start:str = None, 
    end:str = None, 
    ):

    filters = {
        "symbol" : symbol, 
        # "consolidated" : "Consolidated" if consolidated else "Non-Consolidated" 
        }

    if start:
        filters['broadcast_date'] = {
            "$gte" : start,
        }
    if end:
        filters['broadcast_date'] = {
            "$lte" : end
        }

    data = json_util.dumps(db.read(nse_shareholdings, filters))
    return json.loads(data)


@app.get("/nse-announcements-download")
def nse_announcements_download_endpoint(symbol: str):
    nse_announcements_download(symbol)
    return True


@app.get("/nse-announcements-search")
def nse_announcements_search_endpoint(
    symbol:str, 
    keywords: str,
    cutoff_date : str = "2026-01-01"
    ):
    docs = nse_announcements_search(symbol, keywords)

    data = json_util.dumps(docs)
    return json.loads(data)

@app.get('/nse-announcement-extract')
def nse_announcement_extract_endpoint(path_or_url: str):
    
    text = nse_announcement_extract(path_or_url)
    return text

@app.get('/nse-annual-reports-list')
def nse_annual_reports_list_endpoint(symbol: str):
    docs = nse_annual_reports_list(symbol)

    data = json_util.dumps(docs)
    return json.loads(data)

@app.get('/nse-list-annual-report-sections')
def nse_list_annual_report_sections_endpoint(path_or_url : str):
    return nse_list_annual_report_sections(path_or_url)

@app.get('/nse-annual-report-section-download')
def nse_annual_report_section_download_endpoint(
    path_or_url: str,
    keywords : str = "management discussion analysis",
    lag = 0
    ):

    return nse_annual_report_section_download(path_or_url, keywords, lag=lag)

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True )
# import argparse

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from typing import List, Optional

from src.data.manager import DataManager
from src.utils import console
from src.settings import PORT

from pydantic import BaseModel
from typing import List, Optional
from datetime import date

# ____API_CONFIG____

class AnnouncementsRequest(BaseModel):
    ticker : str
    from_date : date # pydantic is supposed to auto convert date strings to date type
    to_date : date
    filter_keyword : str = 'all'

class QuarterlyResultsRequest(BaseModel):
    ticker : str
    from_date : date 
    to_date : date

class ViewerRequest(BaseModel):
    index : str = None
    share : str = None
    symbol : str = None
    data_category : str = None

class ExtractRequest(BaseModel):
    url: str
    symbol: str
    # data_type: str

class DownloadRequest(BaseModel):
    symbol : str
    data_type: str

class ProcessRatiosRequest(BaseModel):
    symbols: List[str]
    period :str = "quarterly"

class TriggerInsertSymbol(BaseModel):
    exchange : str = 'NSE'
    country : str = 'IN'

# ____API_CONFIG____

app = FastAPI(
    title="Voyager Market Intelligence ADMIN",
    description="🛰️⭐ Admin API for Voyager Market Insights. Trigger all download operations from here.",  
    version="1.0.0",               
    docs_url="/docs",           
    openapi_url="/openapi.json", 
    )
origins = ["http://localhost"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ____UTILS____

datamanager = DataManager()

# ____ENDPOINTS____

@app.get("/")
def ping():
    return {"message": f"Voyager running at port {PORT}"}

@app.get("/extract")
async def extract(url: str):
    return datamanager.extract(url=url)

@app.post("/download")
async def scrape_sources(request: DownloadRequest):
    data_type = request.data_type
    symbol = request.symbol

    if data_type == "announcements":
        datamanager.download_announcements(symbol=symbol)
    elif data_type == "results":
        datamanager.download_results(symbol=symbol)
    elif data_type == "shareholding":
        datamanager.download_shareholdings(symbol=symbol)
    elif data_type == "annual_report":
        datamanager.download_annual_reports(symbol=symbol)

    return JSONResponse({"status_code" : 200})

@app.post("/trigger_insert_symbol_pipeline")
def process_ratios(request: TriggerInsertSymbol):
    country = request.country
    exchange = request.exchange

    

    pass


@app.post("/process_fundamentals")
def process_ratios(request: ProcessRatiosRequest):
    
    # check latest quarter/ fy ratios that exists

    # check latest quarter
    # check latest fy results existing

    # if not only then calculate for remaining q/as

    status = datamanager.calculate_fundamentals(
        request.symbols,
        request.period
    )

    return JSONResponse({"status_code" : 200 if status else 500})


@app.post("/process_valuations")
def process_ratios(request: ProcessRatiosRequest):

    # fetch latest valuation dates from db

    # compare with today's date

    # if lagging, pull prices from yf

    # calculate and update

    status = datamanager.calculate_valuations(
        symbols = request.symbols,
        period = request.period
    )

    return JSONResponse({"status_code" : 200 if status else 500})


# ____READER_ENDPOINTS____

@app.get("/announcements")
async def read_announcements(
    symbol : str, 

    from_date : str=None, 
    to_date : str=None
    ):
    data = datamanager.read_announcements(symbol, from_date, to_date)
    return JSONResponse(data)

@app.get("/results")
async def results(
    symbol : str, 
    period = "quarterly",       
    filter_keys:Optional[List[str]] = Query(default=[]),
    from_date : str=None, 
    to_date : str=None,
    filtered=True
    ):
    data = datamanager.read_results(symbol, period, filter_keys, from_date, to_date, filtered=filtered)
    return JSONResponse(data)

@app.get("/shareholdings")
async def shareholdings(
    symbol : str, 
    filter_keys:Optional[List[str]] = Query(default=[]),
    from_date : str=None, 
    to_date : str=None
    ):
    data = datamanager.read_shareholdings(symbol, filter_keys, from_date, to_date)
    return JSONResponse(data)

@app.get("/annual_reports")
async def annual_reports(
    symbol : str, 
    from_date : str=None, 
    to_date : str=None
    ):
    data = datamanager.read_annual_reports(symbol, from_date, to_date)
    return JSONResponse(data)


if __name__ == "__main__":
    uvicorn.run("admin_api:app", host="0.0.0.0", port=8002, reload=True)

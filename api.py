import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
from src.db.connection import init_db
from loguru import logger
from dotenv import load_dotenv

from src.models import ScreenerResponse, TrendlyneResponse, MarketSmithIndiaResponse, SOURCE_MODELS
from src.core import (
    fetch_screener_data,
    fetch_screener_screen,
    fetch_trendlyne_data,
    fetch_stockscans_data,
    fetch_marketsmithindia_data
)
from __version__ import __version__

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beanie via root /db...")
    await init_db()
    yield

app = FastAPI(title="Voyager", version=__version__, lifespan=lifespan)

@app.get("/")
def ping():
    return {"ok": 1}

@app.get("/sources")
async def list_sources():
    return {"sources": list(SOURCE_MODELS.keys())}

@app.get("/schema/{source}")
async def get_schema(source: str):
    model = SOURCE_MODELS.get(source.lower())
    if not model:
        return {"error": f"No model found for source: {source}"}
    return model.model_json_schema()

@app.get("/screener", response_model=ScreenerResponse)
async def screener_endpoint(symbol: str):
    data = await fetch_screener_data(symbol)
    return data

@app.get("/screener/screen")
async def screener_screen_endpoint(url: str):
    data = fetch_screener_screen(url)
    return data

@app.get("/trendlyne", response_model=TrendlyneResponse)
async def trendlyne_endpoint(symbol: str):
    return fetch_trendlyne_data(symbol)

@app.post("/stockscans")
async def stockscans_endpoint(url: str, payload: dict = {}):
    data = fetch_stockscans_data(url, payload)
    return data

@app.get("/marketsmithindia", response_model=MarketSmithIndiaResponse)
def marketsmithindia_endpoint(symbol: str):
    data = fetch_marketsmithindia_data(symbol)
    return data

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True)
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
from db.connection import init_db
from loguru import logger
from dotenv import load_dotenv

from src.tools.screener import Screener
from src.tools.trendlyne import Trendlyne

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beanie via root /db...")
    await init_db()
    yield

app = FastAPI(title="Voyager", version="1.0.0", lifespan=lifespan)

@app.get("/screener")
async def screener_endpoint(symbol: str):
    scr = Screener()
    data = scr.scrape(symbol)
    # Note: We could automatically store it here if needed, but currently let's just return
    return data

@app.get("/trendlyne")
def trendlyne_endpoint(symbol: str):
    tr = Trendlyne()
    return tr.fetch(symbol)

@app.get("/stockscans")
def stockscans_endpoint(symbol: str):
    return {"message": "Stockscans logic not implemented yet", "symbol": symbol}

@app.get("/marketsmithindia")
def marketsmithindia_endpoint(symbol: str):
    return {"message": "Marketsmith India logic not implemented yet", "symbol": symbol}

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True)
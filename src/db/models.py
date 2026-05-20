from beanie import Document
from pydantic import Field
from datetime import datetime
from typing import Optional, Dict, Any

class ScreenerData(Document):
    symbol: str
    source: str = "screener"
    extracted_at: datetime = Field(default_factory=datetime.now)
    data: Dict[str, Any]

    class Settings:
        name = "screener_data"
        indexes = ["symbol"]

class NSEFinancials(Document):
    symbol: str
    consolidated: str
    date: str
    
    class Settings:
        name = "nse-financials"
        indexes = ["symbol", "date"]

class NSEShareholdings(Document):
    symbol: str
    broadcast_date: str
    
    class Settings:
        name = "nse-shareholdings"
        indexes = ["symbol", "broadcast_date"]

from datetime import datetime
from typing import List

from beanie import Document
from pydantic import Field


class NSEStockMetadata(Document):
    symbol: str
    source: str = "NSE"
    last_pull: datetime = Field(default_factory=datetime.now)
    previous_pulls: List[datetime] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    class Settings:
        name = "nse_stock_metadata"
        indexes = ["symbol"]

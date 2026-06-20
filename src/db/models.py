from datetime import datetime
from typing import Any, Dict, List

from beanie import Document
from pydantic import Field


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
    broadcast_date: str | None = None
    financials: List[Dict[str, Any]] = Field(default_factory=list)

    class Settings:
        name = "nse-financials"
        indexes = ["symbol", "date"]


class NSEShareholdings(Document):
    symbol: str
    date: str
    broadcast_date: str

    class Settings:
        name = "nse-shareholdings"
        indexes = ["symbol", "broadcast_date"]


class NSEJobStatus(Document):
    job_id: str
    symbol: str
    status: str = "pending"  # pending, parsing, completed, failed
    total_fetches: int = 0
    completed_fetches: int = 0
    failed_fetches: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    class Settings:
        name = "nse_jobs"
        indexes = ["job_id", "symbol"]


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

from datetime import datetime
from typing import Any, Dict, List

from beanie import Document
from pydantic import Field


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

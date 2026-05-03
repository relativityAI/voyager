from beanie import init_beanie, Document
from pymongo import AsyncMongoClient
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any



class Data(Document):
    source: str
    category: str
    created_at: datetime = Field(default_factory=datetime.now)
    data: dict

    class Settings:
        name = "data"



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


async def init():
    # Create Async PyMongo client
    client = AsyncMongoClient(
        "mongodb://root:example@mongo:27017/"
    )

    # Initialize beanie with the Sample document class and a database
    await init_beanie(database=client, document_models=[Data])

init()
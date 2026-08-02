import os

# Motor 3.7.1 imports `_QUERY_OPTIONS` from `pymongo.cursor`, but newer pymongo
# (>= 4.11) moved it to `pymongo.cursor_shared`. Re-expose it before motor loads.
import pymongo.cursor

if not hasattr(pymongo.cursor, "_QUERY_OPTIONS"):
    try:
        from pymongo.cursor_shared import _QUERY_OPTIONS

        pymongo.cursor._QUERY_OPTIONS = _QUERY_OPTIONS
    except ImportError:
        pass

from beanie import init_beanie
from dotenv import load_dotenv
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .models import NSEJobStatus, NSEStockMetadata

load_dotenv()

_client: AsyncIOMotorClient | None = None
_database: AsyncIOMotorDatabase | None = None


def get_database() -> AsyncIOMotorDatabase:
    if _database is None:
        raise RuntimeError("Database not initialized.")
    return _database


async def init_db():
    global _client, _database
    mongodb_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME")

    if not mongodb_url:
        logger.error("MONGODB_URL not found in environment variables")
        return

    logger.info(f"Connecting to MongoDB at {mongodb_url}...")
    _client = AsyncIOMotorClient(mongodb_url)

    # Motor 3.x attribute access returns a MotorDatabase, which Beanie tries to call.
    # We explicitly set append_metadata to something non-callable to skip Beanie's check.
    _client.append_metadata = None  # type: ignore

    _database = _client[db_name]

    await init_beanie(
        database=_database,
        document_models=[NSEJobStatus, NSEStockMetadata],
    )
    logger.info("Beanie initialization complete.")

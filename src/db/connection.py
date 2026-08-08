import os
from urllib.parse import urlparse

from beanie import init_beanie
from dotenv import load_dotenv
from loguru import logger
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from src.auth.models import APIKey
from src.jobs import PullJob

from .models import NSEStockMetadata

DEFAULT_DB_NAME = "voyager"

load_dotenv()

_client: AsyncMongoClient | None = None
_database: AsyncDatabase | None = None


def get_database() -> AsyncDatabase:
    if _database is None:
        raise RuntimeError("Database not initialized.")
    return _database


async def ping_database() -> bool:
    """Return True if the Mongo client can reach the server."""
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
        return True
    except Exception:
        return False


def hostname_from_url(url: str) -> str:
    """Return only the host[:port] part of a Mongo URL, dropping scheme/credentials."""
    return urlparse(url).netloc.split("@")[-1]


async def init_db():
    global _client, _database
    mongodb_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME") or DEFAULT_DB_NAME

    if not mongodb_url:
        raise RuntimeError(
            "MONGODB_URL is not set. Refusing to start without a database."
        )

    logger.info(f"Connecting to MongoDB at {hostname_from_url(mongodb_url)}...")
    _client = AsyncMongoClient(mongodb_url)
    _database = _client[db_name]

    # Ping to fail fast with a clear message when the DB is unreachable.
    await _client.admin.command("ping")

    await init_beanie(
        database=_database,
        document_models=[NSEStockMetadata, APIKey, PullJob],
    )
    logger.info("Beanie initialization complete.")

"""Fixed-window per-key rate limiting, stored in MongoDB so it is correct
across multiple gunicorn workers/instances."""

import time

from fastapi import HTTPException, status
from pymongo import ReturnDocument

from src.auth.models import APIKey

RATE_LIMIT_COLLECTION = "api_key_usage"


async def check_rate_limit(api_key: APIKey, now: int | None = None) -> None:
    from src.db.connection import get_database

    now = int(time.time()) if now is None else now
    window_start = now // 60 * 60
    database = get_database()
    coll = database[RATE_LIMIT_COLLECTION]
    doc_id = f"{api_key.prefix}:{window_start}"

    updated = await coll.find_one_and_update(
        {"_id": doc_id},
        {"$inc": {"count": 1}, "$setOnInsert": {"window_start": window_start}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if updated["count"] > api_key.rpm:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit exceeded for this key ({api_key.rpm} req/min)",
        )

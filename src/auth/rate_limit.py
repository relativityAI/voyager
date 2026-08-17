"""Fixed-window per-key rate limiting, stored in PostgreSQL so it is correct
across multiple gunicorn workers/instances."""

import time

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.auth.models import APIKey
from src.db.models import APIKeyUsage
from src.db.engine import get_session_factory

RATE_LIMIT_COLLECTION = "api_key_usage"

ADMIN_KEY_CREATE_LIMIT = 10


async def check_rate_limit(api_key: APIKey, now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    window_start = now // 60 * 60
    doc_id = f"{api_key.prefix}:{window_start}"

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            pg_insert(APIKeyUsage)
            .values(id=doc_id, count=1, window_start=window_start)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"count": APIKeyUsage.count + 1},
            )
            .returning(APIKeyUsage.count)
        )
        result = await session.execute(stmt)
        await session.commit()
        count = result.scalar_one()

    if count > api_key.rpm:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit exceeded for this key ({api_key.rpm} req/min)",
        )


async def check_admin_key_rate_limit(now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    window_start = now // 60 * 60
    doc_id = f"admin_key_create:{window_start}"

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            pg_insert(APIKeyUsage)
            .values(id=doc_id, count=1, window_start=window_start)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"count": APIKeyUsage.count + 1},
            )
            .returning(APIKeyUsage.count)
        )
        result = await session.execute(stmt)
        await session.commit()
        count = result.scalar_one()

    if count > ADMIN_KEY_CREATE_LIMIT:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Key creation rate limit exceeded ({ADMIN_KEY_CREATE_LIMIT} keys/min)",
        )

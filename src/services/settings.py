"""Settings service — read/write key-value settings (active pipeline, etc.).

Settings are persisted in Postgres but cached in-process so the (synchronous)
transport never blocks on a database call.  The admin API writes to both DB
and cache together.  On startup, the cache is seeded from DB.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import Setting


_lock = threading.Lock()
_cache: Dict[str, Any] = {}


async def load_settings_from_db() -> None:
    """Seed the in-process cache from the DB (call at startup)."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Setting))
        for row in result.scalars().all():
            with _lock:
                _cache[row.key] = row.value
    logger.info(f"Loaded {len(_cache)} settings from DB")


async def get_setting(key: str) -> Optional[Dict]:
    """Read a setting by key (cache-first, then DB)."""
    with _lock:
        if key in _cache:
            return _cache[key]
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        with _lock:
            _cache[key] = row.value
        return row.value


async def set_setting(key: str, value: Dict) -> Dict:
    """Write a setting to DB and refresh the in-process cache."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            pg_insert(Setting)
            .values(key=key, value=value)
            .on_conflict_do_update(index_elements=["key"], set_={"value": value})
        )
        await session.execute(stmt)
        await session.commit()
    with _lock:
        _cache[key] = value
    return value


def get_setting_sync(key: str) -> Optional[Dict]:
    """Synchronous cache-only read (for the transport layer)."""
    with _lock:
        return _cache.get(key)


__all__ = [
    "get_setting",
    "get_setting_sync",
    "load_settings_from_db",
    "set_setting",
]
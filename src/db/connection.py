from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .engine import Base, async_session, engine

__all__ = ["get_session", "ping_database", "init_db"]

get_session = async_session


async def get_session_dependency() -> AsyncSession:
    """FastAPI dependency that yields an async session."""
    factory = async_session
    if factory is None:
        raise RuntimeError("Database not initialized.")
    async with factory() as session:
        yield session


async def ping_database() -> bool:
    if engine is None:
        return False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def init_db():
    from .engine import init_db as _init_db
    await _init_db()

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = None
async_session = None


class Base(DeclarativeBase):
    pass


def get_session_factory():
    if async_session is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return async_session


async def get_db_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_db():
    global engine, async_session

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Refusing to start without a database."
        )

    logger.info("Connecting to PostgreSQL...")

    engine = create_async_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )

    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    from src.db import models  # noqa: F401 — ensure all models are registered with Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialization complete.")


async def ping_database() -> bool:
    if engine is None:
        return False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def close_db():
    global engine, async_session
    if engine:
        await engine.dispose()
        engine = None
        async_session = None

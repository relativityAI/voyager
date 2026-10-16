import os

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
        # Render's managed Postgres/PgBouncer sets a statement_timeout. On deploy
        # an in-flight pull may still hold a row lock on a table, so a metadata
        # migration waits for it and the timeout turns that benign wait into
        # "canceling statement due to statement timeout" and a boot crash-loop.
        # Disable it for startup DDL only; runtime queries keep the server default.
        await conn.execute(text("SET LOCAL statement_timeout = 0"))

        await conn.run_sync(Base.metadata.create_all)

        # Idempotent schema evolution for columns/constraints on existing tables.
        # create_all() won't ALTER an existing table, so EVERY schema change must
        # be added here — this list is the single migration mechanism.
        for ddl in [

            "ALTER TABLE income_statements ADD COLUMN IF NOT EXISTS cost_of_revenue NUMERIC",
            "ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS cash_flows_from_used_in_investing_activities NUMERIC",
            "ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS cash_flows_from_used_in_financing_activities NUMERIC",
            "ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS payments_for_purchase_of_noncurrent_assets NUMERIC",
            "ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS dividends_paid NUMERIC",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS assets_current NUMERIC",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS inventories NUMERIC",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS trade_receivables_current NUMERIC",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS trade_payables NUMERIC",
            "ALTER TABLE income_statements ADD COLUMN IF NOT EXISTS operating_income NUMERIC",
            "ALTER TABLE income_statements ADD COLUMN IF NOT EXISTS weighted_average_shares_basic NUMERIC",
            "ALTER TABLE income_statements ADD COLUMN IF NOT EXISTS weighted_average_shares_diluted NUMERIC",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS stockholders_equity NUMERIC",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS total_equity_including_nci NUMERIC",
            # Ratio-system columns (Aug 2026) — see FINANCIAL_FIELDS in src/tools/nse/ratios.py
            "ALTER TABLE income_statements ADD COLUMN IF NOT EXISTS depreciation_depletion_and_amortisation_expense NUMERIC",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS current_liabilities NUMERIC",
            # Per-source rows (Sept 2026 SEC/US): source on statement tables,
            # exchange on metadata, one metadata row per (symbol, source).
            "ALTER TABLE income_statements ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE'",
            "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE'",
            "ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE'",
            "ALTER TABLE shareholdings ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE'",
            "ALTER TABLE nse_stock_metadata ADD COLUMN IF NOT EXISTS exchange TEXT",
            "ALTER TABLE nse_stock_metadata ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE'",
            "ALTER TABLE nse_stock_metadata DROP CONSTRAINT IF EXISTS nse_stock_metadata_symbol_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_meta_symbol_source ON nse_stock_metadata (symbol, source)",
            # H1: dedupe key must include source, else NSE and SEC rows for the
            # same (symbol, period_end, consolidated, endpoint) overwrite each
            # other. DROP+CREATE because ADD CONSTRAINT has no IF NOT EXISTS.
            "ALTER TABLE income_statements DROP CONSTRAINT IF EXISTS uq_income_stmt",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_income_stmt ON income_statements (symbol, period_end_date, consolidated, source_endpoint, source)",
            "ALTER TABLE balance_sheets DROP CONSTRAINT IF EXISTS uq_balance_sheet",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_balance_sheet ON balance_sheets (symbol, period_end_date, consolidated, source_endpoint, source)",
            "ALTER TABLE cash_flows DROP CONSTRAINT IF EXISTS uq_cash_flow",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_flow ON cash_flows (symbol, period_end_date, consolidated, source_endpoint, source)",
            "ALTER TABLE shareholdings DROP CONSTRAINT IF EXISTS uq_shareholding",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_shareholding ON shareholdings (symbol, period_end_date, consolidated, source_endpoint, source)",
            # H2: an_dt was TEXT; type it so SQL date ranges/sorts work, then drop
            # the always-NULL sort_date it had replaced. Guarded: ALTER ... TYPE
            # has no IF NOT EXISTS and to_timestamp() fails if already typed.
            """DO $$ BEGIN
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'nse_announcements' AND column_name = 'an_dt') = 'text' THEN
                    ALTER TABLE nse_announcements ALTER COLUMN an_dt TYPE timestamp
                        USING to_timestamp(an_dt, 'DD-Mon-YYYY HH24:MI:SS')::timestamp;
                END IF;
            END $$""",
            "ALTER TABLE nse_announcements DROP COLUMN IF EXISTS sort_date",
            # raw_data JSONB can exceed the btree index limit (~2704 bytes), so
            # dedup on md5(raw_data::text) instead of the full document.
            "DROP INDEX IF EXISTS ux_nse_announcements_raw",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_nse_announcements_raw ON nse_announcements (symbol, md5(raw_data::text))",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_ohlcv ON price_ohlcv (symbol, exchange, date)",
            "CREATE INDEX IF NOT EXISTS idx_price_ohlcv_lookup ON price_ohlcv (symbol, exchange, date)",
            """DO $$ BEGIN
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'stock_data_ledger')
                  AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'data_catalog') THEN
                    ALTER TABLE stock_data_ledger RENAME TO data_catalog;
                END IF;
            END $$""",
            "ALTER TABLE data_catalog DROP CONSTRAINT IF EXISTS uq_stock_data_ledger",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_data_catalog ON data_catalog (symbol, exchange, country)",
            "CREATE TABLE IF NOT EXISTS macro_data (id SERIAL PRIMARY KEY, category TEXT NOT NULL, symbol TEXT, source TEXT NOT NULL DEFAULT 'nse', data JSONB NOT NULL, fetched_at TIMESTAMP NOT NULL DEFAULT now())",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_macro_data ON macro_data (category, symbol, source)",
            "ALTER TABLE price_ohlcv DROP CONSTRAINT IF EXISTS uq_price_ohlcv",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_ohlcv ON price_ohlcv (symbol, exchange, date)",
            "ALTER TABLE price_ohlcv DROP COLUMN IF EXISTS interval",
        ]:
            await conn.execute(text(ddl))

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


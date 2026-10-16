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
            # stock_metadata: rename nse_stock_metadata, drop source in favour of
            # exchange + country (market-agnostic).
            """DO $$ BEGIN
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'nse_stock_metadata')
                  AND EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'stock_metadata')
                  AND (SELECT count(*) FROM stock_metadata) = 0 THEN
                    DROP TABLE stock_metadata;
                END IF;
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'nse_stock_metadata')
                  AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'stock_metadata') THEN
                    ALTER TABLE nse_stock_metadata RENAME TO stock_metadata;
                END IF;
            END $$""",
            "ALTER TABLE stock_metadata DROP COLUMN IF EXISTS source",
            "ALTER TABLE stock_metadata ADD COLUMN IF NOT EXISTS exchange TEXT DEFAULT 'NSE'",
            "ALTER TABLE stock_metadata ADD COLUMN IF NOT EXISTS country TEXT DEFAULT 'IN'",
            "ALTER TABLE stock_metadata DROP CONSTRAINT IF EXISTS nse_stock_metadata_symbol_key",
            "ALTER TABLE stock_metadata DROP CONSTRAINT IF EXISTS uq_stock_meta_symbol_source",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_meta_symbol_exchange_country ON stock_metadata (symbol, exchange, country)",
            # shareholdings: support multiple markets/tickers — add exchange +
            # country and fold them into the dedup key.
            "ALTER TABLE shareholdings ADD COLUMN IF NOT EXISTS exchange TEXT DEFAULT 'NSE'",
            "ALTER TABLE shareholdings ADD COLUMN IF NOT EXISTS country TEXT DEFAULT 'IN'",
            "ALTER TABLE shareholdings DROP CONSTRAINT IF EXISTS uq_shareholding",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_shareholding ON shareholdings (symbol, exchange, country, period_end_date, consolidated, source_endpoint, source)",
            # H1: dedupe key must include source, else NSE and SEC rows for the
            # same (symbol, period_end, consolidated, endpoint) overwrite each
            # other. DROP+CREATE because ADD CONSTRAINT has no IF NOT EXISTS.
            "ALTER TABLE income_statements DROP CONSTRAINT IF EXISTS uq_income_stmt",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_income_stmt ON income_statements (symbol, period_end_date, consolidated, source_endpoint, source)",
            "ALTER TABLE balance_sheets DROP CONSTRAINT IF EXISTS uq_balance_sheet",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_balance_sheet ON balance_sheets (symbol, period_end_date, consolidated, source_endpoint, source)",
            "ALTER TABLE cash_flows DROP CONSTRAINT IF EXISTS uq_cash_flow",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_flow ON cash_flows (symbol, period_end_date, consolidated, source_endpoint, source)",
            # filings_disclosures: rename nse_announcements, make it multi-market,
            # and absorb document_indices (PDF index as a JSONB column).
            # create_all pre-creates the target empty; drop it so the legacy
            # rename can take its place and preserve legacy rows.
            """DO $$ BEGIN
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'nse_announcements')
                  AND EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'filings_disclosures')
                  AND (SELECT count(*) FROM filings_disclosures) = 0 THEN
                    DROP TABLE filings_disclosures;
                END IF;
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'nse_announcements')
                  AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'filings_disclosures') THEN
                    ALTER TABLE nse_announcements RENAME TO filings_disclosures;
                END IF;
            END $$""",
            # an_dt was TEXT; type it so SQL date ranges/sorts work, then rename
            # the NSE field names to market-agnostic ones. Guarded: ALTER ... TYPE
            # has no IF NOT EXISTS and to_timestamp() fails if already typed.
            """DO $$ BEGIN
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'filings_disclosures' AND column_name = 'an_dt') = 'text' THEN
                    ALTER TABLE filings_disclosures ALTER COLUMN an_dt TYPE timestamp
                        USING to_timestamp(an_dt, 'DD-Mon-YYYY HH24:MI:SS')::timestamp;
                END IF;
                IF EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'filings_disclosures' AND column_name = 'an_dt') THEN
                    ALTER TABLE filings_disclosures RENAME COLUMN an_dt TO filed_at;
                END IF;
                IF EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'filings_disclosures' AND column_name = 'attchmnt_text') THEN
                    ALTER TABLE filings_disclosures RENAME COLUMN attchmnt_text TO title;
                END IF;
                IF EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'filings_disclosures' AND column_name = 'desc') THEN
                    ALTER TABLE filings_disclosures RENAME COLUMN "desc" TO description;
                END IF;
                IF EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'filings_disclosures' AND column_name = 'attchmnt_file') THEN
                    ALTER TABLE filings_disclosures RENAME COLUMN attchmnt_file TO url;
                END IF;
                IF EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'filings_disclosures' AND column_name = 'att_file_size') THEN
                    ALTER TABLE filings_disclosures RENAME COLUMN att_file_size TO file_size;
                END IF;
            END $$""",
            "ALTER TABLE filings_disclosures DROP COLUMN IF EXISTS sort_date",
            "ALTER TABLE filings_disclosures ADD COLUMN IF NOT EXISTS exchange TEXT",
            "ALTER TABLE filings_disclosures ADD COLUMN IF NOT EXISTS country TEXT",
            "ALTER TABLE filings_disclosures ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE'",
            "ALTER TABLE filings_disclosures ADD COLUMN IF NOT EXISTS filing_type TEXT DEFAULT 'announcement'",
            "ALTER TABLE filings_disclosures ADD COLUMN IF NOT EXISTS market TEXT",
            "ALTER TABLE filings_disclosures ADD COLUMN IF NOT EXISTS pdf_index JSONB",
            "ALTER TABLE filings_disclosures ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()",
            # Backfill the market columns for rows that predate them.
            "UPDATE filings_disclosures SET source = 'NSE' WHERE source IS NULL",
            "UPDATE filings_disclosures SET country = 'IN' WHERE country IS NULL",
            "UPDATE filings_disclosures SET exchange = 'NSE' WHERE exchange IS NULL",
            "UPDATE filings_disclosures SET filing_type = 'announcement' WHERE filing_type IS NULL",
            # raw_data JSONB can exceed the btree index limit (~2704 bytes), so
            # dedup on md5(raw_data::text). Documents dedup on their URL; the
            # index is partial because announcements reuse attachment URLs.
            "DROP INDEX IF EXISTS ux_nse_announcements_raw",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_filing_raw ON filings_disclosures (symbol, source, md5(raw_data::text))",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_filing_url ON filings_disclosures (source, url) WHERE filing_type = 'document'",
            # document_indices is now folded into filings_disclosures.
            "DROP TABLE IF EXISTS document_indices",
            "DROP TABLE IF EXISTS nse_annual_reports",
            # news split: market-wide macro_news (renamed) + per-symbol stock_news.
            """DO $$ BEGIN
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'news_articles')
                  AND EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'macro_news')
                  AND (SELECT count(*) FROM macro_news) = 0 THEN
                    DROP TABLE macro_news;
                END IF;
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'news_articles')
                  AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'macro_news') THEN
                    ALTER TABLE news_articles RENAME TO macro_news;
                END IF;
            END $$""",
            "CREATE TABLE IF NOT EXISTS stock_news (id SERIAL PRIMARY KEY, symbol TEXT NOT NULL, exchange TEXT, country TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL, title TEXT, summary TEXT, published_at TIMESTAMP, fetched_at TIMESTAMP NOT NULL DEFAULT now())",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_news_symbol_source_url ON stock_news (symbol, source, url)",
            # Renaming the table keeps the legacy index name; align it with the model.
            "ALTER INDEX IF EXISTS uq_news_article_url RENAME TO uq_macro_news_url",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_ohlcv ON price_ohlcv (symbol, exchange, date)",
            "CREATE INDEX IF NOT EXISTS idx_price_ohlcv_lookup ON price_ohlcv (symbol, exchange, date)",
            """DO $$ BEGIN
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'stock_data_ledger')
                  AND EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'data_catalog')
                  AND (SELECT count(*) FROM data_catalog) = 0 THEN
                    DROP TABLE data_catalog;
                END IF;
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'stock_data_ledger')
                  AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'data_catalog') THEN
                    ALTER TABLE stock_data_ledger RENAME TO data_catalog;
                END IF;
            END $$""",
            "ALTER TABLE data_catalog DROP CONSTRAINT IF EXISTS uq_stock_data_ledger",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_data_catalog ON data_catalog (symbol, exchange, country)",
            # data_catalog: per-type record counts + last-pull timestamps.
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS income_statement_records INTEGER DEFAULT 0",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS income_statement_last_updated TIMESTAMP",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS balance_sheet_records INTEGER DEFAULT 0",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS balance_sheet_last_updated TIMESTAMP",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS cash_flow_records INTEGER DEFAULT 0",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS cash_flow_last_updated TIMESTAMP",
            # RENAME COLUMN has no IF EXISTS, so guard on information_schema.
            # The legacy table carries BOTH filing_records and announcement_records;
            # collapse to one, dropping the duplicate rather than colliding.
            """DO $$ BEGIN
                IF EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'data_catalog' AND column_name = 'announcement_records') THEN
                    IF NOT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'data_catalog' AND column_name = 'filing_records') THEN
                        ALTER TABLE data_catalog RENAME COLUMN announcement_records TO filing_records;
                    ELSE
                        ALTER TABLE data_catalog DROP COLUMN announcement_records;
                    END IF;
                END IF;
                IF EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'data_catalog' AND column_name = 'announcement_last_updated') THEN
                    IF NOT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'data_catalog' AND column_name = 'filing_last_updated') THEN
                        ALTER TABLE data_catalog RENAME COLUMN announcement_last_updated TO filing_last_updated;
                    ELSE
                        ALTER TABLE data_catalog DROP COLUMN announcement_last_updated;
                    END IF;
                END IF;
            END $$""",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS news_records INTEGER DEFAULT 0",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS news_last_updated TIMESTAMP",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS social_media_records INTEGER DEFAULT 0",
            "ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS social_media_last_updated TIMESTAMP",
            "DROP TABLE IF EXISTS macro_data",
            "ALTER TABLE price_ohlcv DROP CONSTRAINT IF EXISTS uq_price_ohlcv",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_ohlcv ON price_ohlcv (symbol, exchange, date)",
            "ALTER TABLE price_ohlcv DROP COLUMN IF EXISTS interval",
            # Removed ledgers (no longer used).
            "DROP TABLE IF EXISTS stock_data_ledger",
            "DROP TABLE IF EXISTS pull_jobs",
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


-- SEC/US data (Sept 2026): new source column on statement tables, exchange on
-- metadata, metadata unique (symbol) -> (symbol, source).
-- create_all() only adds missing tables, so new columns need explicit ALTERs.

ALTER TABLE income_statements
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE';

ALTER TABLE balance_sheets
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE';

ALTER TABLE cash_flows
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE';

ALTER TABLE shareholdings
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'NSE';

ALTER TABLE nse_stock_metadata
    ADD COLUMN IF NOT EXISTS exchange TEXT;

-- Backfill existing rows to NSE (safe: NSE was the only writer).
UPDATE income_statements  SET source = COALESCE(source, 'NSE');
UPDATE balance_sheets     SET source = COALESCE(source, 'NSE');
UPDATE cash_flows         SET source = COALESCE(source, 'NSE');
UPDATE shareholdings      SET source = COALESCE(source, 'NSE');

-- Metadata now permits one row per (symbol, source). Drop the old single-symbol
-- uniqueness only if it exists under the default auto name, then add the pair.
ALTER TABLE nse_stock_metadata
    DROP CONSTRAINT IF EXISTS nse_stock_metadata_symbol_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_meta_symbol_source
    ON nse_stock_metadata (symbol, source);
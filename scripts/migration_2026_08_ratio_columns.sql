-- Ratio-system columns (Aug 2026): see FINANCIAL_FIELDS in src/tools/nse/ratios.py
-- create_all() only adds missing tables, so new columns need explicit ALTERs.

ALTER TABLE income_statements
    ADD COLUMN IF NOT EXISTS depreciation_depletion_and_amortisation_expense NUMERIC;

ALTER TABLE balance_sheets
    ADD COLUMN IF NOT EXISTS current_liabilities NUMERIC;

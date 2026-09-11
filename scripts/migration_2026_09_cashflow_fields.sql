-- Cash-flow completeness (Sept 2026): investing/financing flows + CapEx so
-- DCF can use true FCF (OCF - CapEx) instead of OCF as a proxy.
-- create_all() only adds missing tables, so new columns need explicit ALTERs
-- (also mirrored in src/db/engine.py init_db() for self-healing deploys).

ALTER TABLE cash_flows
    ADD COLUMN IF NOT EXISTS cash_flows_from_used_in_investing_activities NUMERIC;

ALTER TABLE cash_flows
    ADD COLUMN IF NOT EXISTS cash_flows_from_used_in_financing_activities NUMERIC;

ALTER TABLE cash_flows
    ADD COLUMN IF NOT EXISTS payments_for_purchase_of_noncurrent_assets NUMERIC;
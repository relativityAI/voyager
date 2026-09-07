# SEC Integration — Decision Log

This file records the decisions made while adding US equities (SEC/EDGAR) as a
second data source to Voyager, and the reasoning behind each. It exists so the
change history can be reviewed without re-deriving the context.

---

## D-01 — Add a `source` column to existing statement tables instead of US-specific tables

**Status:** Decided (approved by user)
**Date:** 2026-09-07

**Decision:** Reuse the four NSE statement tables with a `source` column
(`'NSE'` default, `'SEC'`) plus a `(symbol, source)` unique key on
`nse_stock_metadata` (which also gains `exchange` for Yahoo quote resolution).

**Why:**
- Read/API paths (`get_financials`, `get_statement_data`, `get_pull_status`,
  metrics) already exist and are source-agnostic; a shared table means one
  query path per endpoint, not parallel US tables.
- Keeps the migration tiny (one nullable column + one unique constraint swap).

**Trade-offs considered:**
- Separate US tables would avoid touching NSE queries but double the surface
  area and the metrics merge logic. Rejected.
- Queries must now filter by `source` — a constant, covered by existing indexes.

## D-02 — Use edgartools as the EDGAR data layer

**Status:** Decided (approved by user)
**Date:** 2026-09-07

**Decision:** Pull XBRL via the `edgartools` library instead of writing an
EDGAR filings/XML client.

**Why:**
- edgartools handles EDGAR's 10 req/s throttle, JSON index pagination,
  8-K/Form-4/XBRL object models, and multi-filing statement stitching
  (`XBRLS.from_filings`).
- No EDGAR-specific transport code to maintain.

**Trade-offs considered:**
- A hand-rolled client gives full control of request scheduling but is a
  large, perpetual maintenance surface for one feature. Rejected.
- Relying on the library means its behavior drives ours (e.g. identity:
  EDGAR requires a declarative UA, exposed via `SEC_IDENTITY` env var).

## D-03 — Map raw `us-gaap_*` concept tags, not `standard_concept`

**Status:** Decided
**Date:** 2026-09-07

**Decision:** Statement rows map from raw `concept` tags (e.g.
`us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax`) via per-field
alias lists.

**Why:**
- `standard_concept` is frequently `None` on the XBRLS frames, making it an
  unreliable key.
- Raw `us-gaap_*` tags are stable identifiers; alias lists absorb company
  tagging differences.

## D-04 — Derive single-quarter values from cumulative-YTD 10-Q frames by differencing

**Status:** Decided
**Date:** 2026-09-07

**Decision:** For 10-Q income/cash-flow, single-quarter value = current period
column − previous period column within the same fiscal year. A period gap
larger than 125 days marks a new fiscal year (the column is already that
quarter's value). Balance sheets need no differencing.

**Why:**
- US 10-Qs report cumulative YTD figures per fiscal year; the DB schema and
  metrics assume per-period (single-quarter) values.

**Trade-offs considered:**
- Per-filing parsing (`filing.xbrl()`) exposes true single-quarter `(Q)` and
  `(YTD)` columns directly (used to validate the differencing numerically)
  but is a bigger rewrite of the validated `XBRLS.from_filings` pipeline.
  Rejected for now.
- EPS derived by differencing cumulative EPS is an approximation that sums
  correctly over four quarters (TTM EPS is exact).

## D-05 — Derive the missing fiscal-year-end quarter from the 10-K

**Status:** Decided
**Date:** 2026-09-07

**Decision:** A fiscal year's Q4 quarter is reported only in the 10-K. Voyager's
quarterly pull additionally parses the 10-K frames and stores, per fiscal year,
`Q4 = 10-K FY column − matching Q3 10-Q YTD column` as a normal `10-Q`
quarterly row (same `source_endpoint` as other quarters, so it coexists with
the `10-K` annual row for that period).

**Why:**
- Without Q4 the trailing-four-quarter TTM window is misaligned (the 12-month
  window reaches back to Q3 of the prior year instead of Q4), skewing every
  TTM/growth metric.

**Trade-offs considered:**
- Mixing 10-K and 10-Q columns into one stitched frame breaks the gap-based
  fiscal-year heuristic (FY-end vs Q1 columns are both 3 months apart). Rejected.
- Accepting the omission was rejected for correctness reasons.
- Q4 EPS is estimated as `Q4 net income ÷ fiscal-year weighted shares`.

## D-06 — Reject country/source combos centrally in `_validate_source`

**Status:** Decided
**Date:** 2026-09-07

**Decision:** `src/services/_common.py` owns the supported matrix
(`{"in": ("NSE",), "us": ("SEC",)}`) and raises `UnsupportedSourceError`
(→ 501) for anything else. Pull jobs store `country`/`source` and dispatch to
`pull_sec_data` when source is `SEC`.

**Why:**
- One guard in the shared validation beats a guard in every caller; pull and
  read paths behave identically.

## D-07 — US announcements = 8-K filings; US shareholdings = Form 4 insider aggregation

**Status:** Decided
**Date:** 2026-09-07

**Decision:** `GET /announcements` for SEC returns the latest 8-K filings
(dated, accessioned, URL). `GET /shareholdings` for SEC keys off the US source
and returns a **US-specific schema** (Forms 3/4/5 insider aggregation) instead
of the India promoter/FII/DII pattern:

```json
{ "symbol": "GOOG", "source": "SEC",
  "shareholdings": { "source": "SEC", "source_endpoint": "Forms 3/4/5",
    "as_of": "2026-08-12", "insider_shares": 361973756.0,
    "shares_outstanding": 5527000000.0, "insider_ownership_pct": 6.55,
    "note": "SEC Forms 3/4/5 insider holdings" } }
```

**Why:**
- No SEC equivalent of NSE announcements or promoter holdings exists; these
  are the closest analogues.
- India pattern fields (promoters, FII, DII, public shareholding) are
  meaningless for US issuers — reusing one schema meant unremovable `null`s.
  Following the "when data differs, use a different schema" rule, SEC returns
  its own shape; NSE keeps the India shape.
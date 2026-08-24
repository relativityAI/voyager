# Pipeline & Schema Decisions — Log

Date of this work: **2026-08-24**

This file records the decisions made to add scraping pipelines, fix NSE
endpoints, add price/volume data, and make the stock schema scalable to other
exchanges and markets (e.g. US SEC filings). It explains the *why* in simple
terms so the history can be reviewed later.

---

## S-01 — Target PostgreSQL, not MongoDB

**Decision:** Build on the existing PostgreSQL + SQLAlchemy stack.

**Why:** The README says MongoDB, but the live code (`src/db/models.py`) is
SQLAlchemy / Postgres. Building on what the app already runs on avoids a
migration and keeps everything consistent.

---

## S-02 — Add a generic layer; keep the existing flat tables

**Decision:** Add new generic `Statement` and `Fact` tables alongside the
existing flat `income_statements` / `balance_sheets` / `cash_flows` tables.
Do not migrate or rewrite the working flat tables.

**Why:** The flat tables work and scraping currently depends on them. Changing
them is risky. The new generic layer is purely additive, so nothing breaks.
It lets future markets (SEC 10-K / 10-Q) map to the same normalized
(statement + fact) shape rather than needing brand-new tables per market.

---

## S-03 — Store the active pipeline configuration in Postgres

**Decision:** Add a `settings` key-value table; the active scraping pipeline
(direct vs residential proxy) is stored there and editable via an admin API
endpoint.

**Why:** A per-machine JSON file does not survive re-deploys and is not shared
between client and server. Postgres does. The admin panel already talks to the
API with an admin key, so a settings endpoint fits the existing design.

---

## S-04 — Scraping pipelines: direct + residential; remove the free proxy pool

**Decision:** Introduce a `Pipeline` abstraction with two implementations:
- **Pipeline 1 (direct):** no proxy (current local behaviour).
- **Pipeline 2 (residential):** a configurable residential proxy gateway
  (URL + optional username/password). Provider not finalized yet, so this is
  built as a seam (env/config fields) with generic user:pass @ host:port
  formatting. Provider-specific logic is added later when the provider is
  chosen — not speculatively now.

Remove the free `ProxyPool` code. The existing static `NSE_PROXY` override keeps
working as part of the direct pipeline.

**Why:** free public proxies are unreliable and the user asked to remove them.
gIn its builder.

---

## S-05 — Price & volume: NSE endpoints primary, yfinance fallback

**Decision:** Fetch live quotes and historical OHLC/volume from NSE's own
`api/quote-equity` and `api/historical/cm/equity` endpoints, store them in a new
`market_data` table. Keep yfinance as a fallback when NSE is unreachable.

**Why:** yfinance is rate-limited and goes through a third party anyway.
NSE is the authoritative, same-origin source and integrates with the existing
anti-detection transport.

---

## S-06 — Store 3 years of price history per symbol

**Decision:** Backend `market_data` history is fetched for / kept 3 years back
per symbol.

**Why:** this length covers 52-week / 3-month / yearly technical windows without
storing an unbounded series.

---

## S-07 — Keep `pull_nse_data` (existing scrape) untouched; add new pieces additively

**Decision:** `src/services/nse.py:pull_nse_data` is not modified. Price/volume
and generic-fact writing are added at the pull-job layer (`src/jobs.py`) as a
guarded, additive step.

**Why:** this function is the core of the currently working scrape and is under
test. Leaving it unchanged protects against breaking existing scraping.

---

<!-- Append further decisions here as implementation proceeds -->
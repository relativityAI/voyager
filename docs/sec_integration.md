# SEC Integration (US equities)

Voyager serves US company fundamentals from SEC EDGAR XBRL, reusing the NSE
statement tables and API shapes via a `source` column.

## How it works

- Data comes from **edgartools** (EDGAR XBRL → pandas), no scraping of
  sec.gov directly. edgartools enforces EDGAR's 10 req/s limit internally.
- Statements land in the same four tables as NSE (`income_statements`,
  `balance_sheets`, `cash_flows`, `shareholdings`-adjacent) plus
  `nse_stock_metadata`, all with `source = 'SEC'`:
  - `income_statements` / `balance_sheets` / `cash_flows` — one row per period.
  - `nse_stock_metadata` — symbol + `exchange` (e.g. `NASDAQ`) so Yahoo quotes
    resolve; keyed on `(symbol, source)`.
- A pull (`POST /pull?symbol=AAPL&source=sec`) fetches, in
  background, the last `EDGAR_MAX_QUARTERLY_FILINGS` 10-Qs and
  `EDGAR_MAX_ANNUAL_FILINGS` 10-Ks.

### Period semantics (why the differencing)

US filings are **cumulative YTD per fiscal year** (Q2 = 6 months, Q3 = 9
months), unlike NSE's per-quarter statements:

- **10-Q income & cash-flow** — cumulative YTD. Single-quarter values are
  derived by subtracting the previous quarter's column in the same fiscal
  year (a ~6-month gap marks a new fiscal year).
- **10-K income & cash-flow** — full-year totals.
- **Balance sheets** — point-in-time instants, no differencing needed.

### The fiscal-year-end (Q4) quarter

10-Qs only cover three quarters per fiscal year; the Q4 quarter (e.g. the
September quarter for Apple) is reported **only** in the 10-K, as a full-year
column minus the Q3 YTD. Voyager derives it per fiscal year:

```
Q4 = 10-K FY total − 10-Q 9-month YTD (from the matching Q3 filing)
```

and stores it as a regular `10-Q` quarterly row, so the quarterly series has
all four quarters of each fiscal year. This makes trailing-twelve-month (TTM)
sums correct.

## Mapped fields

Each statement table field maps to US GAAP tags (see `_INCOME_MAP`,
`_BALANCE_MAP`, `_CASHFLOW_MAP` in `src/services/sec.py`). High-level fields
`revenue_from_operations`, `profit_loss_for_period`, `assets`, OCF, etc. are
populated; the granular NSE fields that have no US GAAP equivalent
(depreciation detail, current assets, dividends) stay `null`.

## API surface

All endpoints take a single `source` param; `source=sec` selects the US/EDGAR
path (the country is derived from the source — no `country` param).

- Read financials / statements / metrics: `symbol=<TICKER>&source=sec`.
- `announcements` — 8-K filings (dated, with sec.gov URLs).
- `shareholdings` — a US-specific response (Forms 3/4/5 insider ownership):
  `insider_ownership_pct`, `insider_shares`, `shares_outstanding`, `as_of`,
  `source_endpoint: "Forms 3/4/5"`. It replaces the India promoter/FII/DII
  pattern rather than returning it nulled out.
- Unknown sources return `501`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SEC_IDENTITY` | *(unset)* | `"Name email"` — EDGAR UA declaration. **Required**; without it the pull fails with a clear EDGAR error. |
| `EDGAR_MAX_ANNUAL_FILINGS` | `8` | 10-K filings parsed per pull |
| `EDGAR_MAX_QUARTERLY_FILINGS` | `40` | 10-Q filings parsed per pull (≈ 13 years of quarters) |

## Known limitations

- Granular NSE-only fields are `null` for US symbols (tag mapping covers the
  top-level fields only).
- `fiscal_period` uses the repo's calendar convention (same as NSE) rather
  than each filer's fiscal calendar — a display field only.
- EPS for the derived fiscal-year-end quarter is computed as
  `Q4 net income ÷ fiscal-year weighted shares` (approximation).

See `docs/sec_integration_decisions.md` for the design decisions and rejected
alternatives.
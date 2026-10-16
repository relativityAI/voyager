# Voyager

A financial data API for Indian equities (NSE) and US equities (SEC) that turns exchange XBRL filings into investment metrics.

## What you get

Point it at a stock symbol and Voyager returns:

**Market data** — current price, 52-week high/low, volume, RSI, moving averages (SMA/EMA), Bollinger Bands, ATR.

**Valuation** — P/E, P/B, P/S, EV/EBITDA, EV/Revenue, PEG, FCF yield, market cap, enterprise value.

**Profitability & efficiency** — net/operating margin, ROE, ROA, ROIC, asset turnover, cash & operating cash-flow ratios.

**Growth** — YoY growth for revenue, earnings, EPS, FCF, operating income, book value.

**Solvency** — debt-to-equity, debt-to-assets, interest coverage.

**Per share** — EPS, book value per share, FCF per share.

**Shareholding** — promoter, FII, DII, and public holding from the latest filing.

**Raw statements** — the actual quarterly/annual income statements, balance sheets, and cash flows pulled from NSE or SEC (EDGAR) filings.

## Quick start

```
GET /financial-metrics?symbol=VBL&filing_type=ttm
```

Try it in your browser or with curl:

```bash
curl -H "X-API-Key: $VOYAGER_API_KEY" \
  "http://localhost:8001/financial-metrics?symbol=VBL&filing_type=ttm"
```

`filing_type` can be `quarterly`, `annual`, or `ttm` (trailing twelve months). Full docs at `http://localhost:8001/docs`.

Data endpoints are protected by service API keys (`X-API-Key` header). Health endpoints (`/`, `/healthz`, `/readyz`, `/metrics`) are public. See [API keys](#api-keys) and the [remote client](#remote-client-cli).

---

## Response template

A `/financial-metrics` response looks like this (values are examples; `null` means the data isn't in the filings):

> `price_data` is `"live"` when the live quote provider (Yahoo Finance) was reachable and `"unavailable"` when it wasn't (e.g. rate-limited). When `"unavailable"`, the price-derived fields (`current_price`, market cap, valuation ratios) are omitted rather than crashing the request.
>
> This endpoint returns financial metrics only. Technical indicators live at [`/technicals`](#technicals--multi-timeframe-technical-report). `null` values are omitted throughout: an absent key means "not computable for this issuer".

```json
{
  "symbol": "VBL",
  "last_quarter_end_date": "2026-06-30",
  "consolidated": true,
  "filing_type": "ttm",
  "price_data": "live",

  "current_price": 442.3,

  "market_capitalization": 1496000000000.0,
  "enterprise_value": 1499000000000.0,
  "price_to_earnings_ratio": 44.19,
  "price_to_book_ratio": 6.87,
  "price_to_sales_ratio": 6.04,
  "enterprise_value_to_ebitda_ratio": 32.47,
  "peg_ratio": 2.51,

  "ebitda_margin": 22.0,
  "operating_margin": 16.0,
  "net_margin": 12.0,
  "return_on_equity": 15.66,
  "return_on_assets": 11.29,
  "return_on_invested_capital": 17.14,
  "asset_turnover": 0.9,

  "current_ratio": 3.1,
  "quick_ratio": 2.4,
  "debt_to_equity": 0.16,
  "interest_coverage": 23.56,

  "revenue_growth_annual": 14.64,
  "earnings_growth_annual": 18.32,
  "earnings_per_share_growth_annual": 17.6,

  "earnings_per_share": 10.01,
  "book_value_per_share": 64.36,
  "free_cash_flow_per_share": 7.5,
  "payout_ratio": 18.2,

  "total_debt": 5000000000.0,
  "total_equity": 217000000000.0,
  "cash_and_equivalents": 15000000000.0
}
```

---

## Technical reference

### Setup

```bash
pip install -r requirements.txt
python api.py                 # serves on 0.0.0.0:8001 (reload on in dev)
```

Configure via `.env` (copy from `.env.example`):

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL, e.g. `postgresql+asyncpg://postgres:example@localhost:5432/voyager` |
| `SENTRY_DSN` | *(unset)* | Enables Sentry error tracking when set |
| `CORS_ORIGINS` | *(unset)* | Comma-separated list; CORS middleware only added when set |
| `LOG_FILE_SINK` | *(unset)* | When set, logs also go to this file path |
| `SEC_IDENTITY` | *(unset)* | `"Name email"` required for SEC pulls (EDGAR UA declaration; throttled to 10 req/s internally) |
| `EDGAR_MAX_ANNUAL_FILINGS` | `8` | 10-K filings parsed per SEC pull (annual statements + fiscal-year-end quarter derivation) |
| `EDGAR_MAX_QUARTERLY_FILINGS` | `40` | 10-Q filings parsed per quarterly SEC pull |
| `ENVIRONMENT` | `development` | `production` disables uvicorn reload |
| `WEB_CONCURRENCY` | `2` | gunicorn workers (Render free: `1`) |

### Endpoints

Data endpoints require an API key via the `X-API-Key` (or `Authorization: Bearer`) header. Auth: 🔓 public · 🔑 any valid key (scope `data:read`). For AI agents: `/llms.txt` is the short guide, `/openapi.json` the full contract (auth schemes, enums, response schemas); errors are RFC 9457 `application/problem+json` with a stable `code`.

| Endpoint | Auth | Description |
|---|---|---|
| `GET /` | 🔓 | Health check: `{"ok": 1}` |
| `GET /healthz` | 🔓 | Liveness probe |
| `GET /readyz` | 🔓 | Readiness probe — 503 when the DB is unreachable |
| `GET /metrics` | 🔓 | Prometheus metrics (default on; disable with `METRICS_ENABLED=false`) |
| `GET /search-categories?category=sources` | 🔑 | Available categories: `sources`, `countries` |
| `GET /search?q=relian` | 🔑 | Case-insensitive symbol search over the DB, ranked by data coverage |
| `GET /financials?symbol=VBL` | 🔑 | Latest income + balance + cash-flow merged into one doc |
| `GET /financials/income-statements` | 🔑 | Raw statement rows (also `balance-sheets`, `cash-flows`) |
| `GET /financial-metrics?symbol=VBL` | 🔑 | Computed metrics (this repo's core). `&fields=pe_field,roe_field` keeps only those metrics (+ identifier meta) |
| `GET /financial-metrics/batch?symbols=VBL,TCS` | 🔑 | Same, for up to 10 symbols in one call; per-symbol errors don't fail the batch |
| `GET /history?symbol=VBL&period=1y&interval=1d` | 🔑 | Raw OHLCV price history (daily bars) |
| `GET /prices/ohlcv?symbol=VBL&exchange=NSE` | 🔑 | Stored OHLCV (daily/weekly/monthly via `interval`) |
| `GET /technicals?symbol=VBL&timeframes=daily,weekly,monthly` | 🔑 | End-to-end technical analysis report: multi-timeframe indicators, structure, patterns, levels, scenarios. Sections degrade to `unsupported` with a reason instead of failing |
| `GET /announcements?symbol=VBL&market=equities` | 🔑 | Corporate announcements (`equities` or `sme`) |
| `GET /shareholdings?symbol=VBL` | 🔑 | Latest promoter / FII / DII / public holding pattern |
| `GET /news/stories?country=in` | 🔑 | Latest news stories for a market (`us`/`in`, RSS, cached) |
| `GET /news/ticker?symbol=VBL` | 🔑 | News stories mentioning a symbol |
| `GET /social/reddit?query=` | 🔑 | Reddit mentions for a ticker/company |
| `GET /social/youtube/search?query=` | 🔑 | YouTube search (e.g. earnings calls) |
| `GET /social/youtube/transcript?video_id=` | 🔑 | Captions/transcript for a video |
| `GET /documents/pages?url=&pages=` | 🔑 | Per-page text for selected pages of an indexed PDF (`pages=3,5-9`) |
| `GET /llms.txt` | 🔓 | llms.txt guide for AI agents (auth, conventions, quick start) |

> The advanced suite also serves `GET /technicals` (61-section technical analysis report) and `GET /technicals/chart` (price chart PNG) — see the [Advanced Data Suite](#advanced-data-suite-1) tag in `/docs`.

Financial endpoints take a single `source` query param (`source=sec` for US/EDGAR, `source=nse` for India/NSE, the default); the country is derived from the source. An unknown source returns `501`.

**Not-found convention**: a symbol with no pulled data returns `200` with `"data_available": false` from metrics endpoints (so batch calls never break), while endpoints that address a single known resource (documents) return `404`.

```bash
curl -H "X-API-Key: $VOYAGER_API_KEY" \
  "http://localhost:8001/financial-metrics?symbol=AAPL&source=sec"
```

SEC ingestion requires `SEC_IDENTITY` (see [ENV](#environment)) and is documented in [docs/sec_integration.md](docs/sec_integration.md).

### API keys

Service keys are managed from the Streamlit admin panel (**API Keys** tab talks to the database directly). The raw key is shown **once** at creation; only its SHA-256 hash and 12-char prefix are stored.

Scopes: `data:read` (all reads). Optional `rpm` rate cap (default 60 req/min, enforced with a fixed-window counter) and expiry via the panel.

### Data ingestion

The API is **read-only** — every DB write lives in the `src.ingest` package. Run ingests from the interactive CLI's **Ingest** section (pick it from the home menu, or jump straight in):

```bash
python cli.py           # home menu → Ingest
python cli.py --ingest  # skip the menu
```

Pick an item — financials / prices / news / shareholdings / documents — then choose **single symbol** or **index file** to pull in bulk from the lists in `data/indices/` (`nifty`, `midcap`, `smallcap`, `nasdaq`, `snp`). Every prompt is a numbered menu (filing type, source, exchange, refresh, and a symbol picker that lists the index files' tickers), so you never type a path or flag by hand. Rows are upserted on their natural keys, so re-running an ingest is cheap and idempotent. Unless you answer **Yes** to *refresh stored data*, a symbol pulled within the last 7 days is skipped instantly (`skipped (fresh)`) instead of re-downloaded — both the NSE and SEC paths gate before any network call. A pull reports its breakdown on one line — e.g. `957 records · 7 income 7 balance 4 cashflow`.

### Remote client CLI

`client/` is a standalone Typer CLI that talks to any deployed Voyager over HTTPS. Install and use it from your main app or laptop:

```bash
pip install -r client/requirements.txt

export VOYAGER_BASE_URL=https://voyager-1hpq.onrender.com
export VOYAGER_API_KEY=vgr_...          # minted via the admin panel

python -m client ping
python -m client metrics VBL --filing-type ttm
python -m client financials VBL --all-fields
python -m client statements VBL income --limit 4
python -m client announcements VBL
python -m client shareholdings VBL
python -m client list-categories
python -m client technicals VBL --out report.json   # end-to-end technical analysis
```

### Deployment (Render)

This repo ships a `render.yaml` blueprint for Render. The API is **read-only**: **Render serves reads**; **ingestion runs locally** via `python cli.py` → *Ingest* (see [Data ingestion](#data-ingestion)) and writes into the same Postgres database. This keeps the Render instance cheap and stateless.

1. **PostgreSQL** — Supabase (free tier) or any Postgres. Create a DB user and copy the connection string (asyncpg driver: `postgresql+asyncpg://...`). Allow the ingester's IP.
2. **Render** — New + → Blueprint, select this repo (it auto-detects `render.yaml`), or create a Web Service manually (Docker, `./Dockerfile`, plan: free, region near your users).
3. **Secrets** — on the Render Dashboard set:
   - `DATABASE_URL` = your Postgres URI
   - optionally `SENTRY_DSN`, `CORS_ORIGINS`, `SEC_IDENTITY`
4. **Deploy** — Render builds and starts gunicorn; the health check hits `/healthz`. Verify with `curl https://<service>.onrender.com/readyz` (should return `{"ok": true}`).
5. **Create a key for your main app** (see [API keys](#api-keys)), then put `VOYAGER_BASE_URL` + `VOYAGER_API_KEY` in your app's config.
6. **Load data** — run ingestion locally via `python cli.py` → *Ingest* (writes into the Postgres DB that Render serves).

#### Monitoring

- **Sentry** — set `SENTRY_DSN` and unhandled exceptions report automatically (environment-gated; skip for dev).
- **Grafana Cloud** — add a Prometheus data source, then scrape `https://<service>.onrender.com/metrics`. Key metrics: `http_requests_total` and `http_request_duration_seconds` (both labeled by method/route/status), plus the standard Python/process collectors (`python_info`, `process_*`). Health probes already expose `/readyz` for uptime alerts.

### Key parameters

- `consolidated` — `true` (default) for consolidated statements, `false` for standalone, `null` for both. Periods where the issuer only filed standalone still appear under `true` — they fall back to the standalone filing, which is the whole-company picture at that time.
- `filing_type` — `quarterly`, `annual`, or `ttm`.
- `refresh` — on ingest, forces a re-download and re-parse of XBRL already in the DB.
- `limit` — on statement endpoints, number of rows to return (0 = all).
- `all_fields` — return every stored field instead of only priority metrics.

### How metrics are computed

- **TTM** sums the latest 4 quarterly income/cash-flow records and compares against the prior 4-quarter window for growth. Balance-sheet metrics use the latest quarter's point-in-time values.
- **FCF** ≈ operating cash flow (capex isn't in the filings), so `free_cash_flow_yield` = OCF ÷ market cap.
- **PEG** uses 3Y EPS CAGR when positive, otherwise 1Y EPS growth.
- **EV/EBITDA** proxies EBITDA with EBIT (no depreciation data).
- Metrics that need data Voyager doesn't collect (COGS, current assets, dividends) return `null`.

### Notes

- Financial endpoints serve India/NSE and US/SEC, selected via a single `source` param (`nse` default, or `sec`); the codebase also contains scrapers for web screeners (Screener, Trendlyne, StockScans), news/blog sources, and market data APIs, exposed via the `cli.py` tooling.

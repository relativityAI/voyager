# Voyager

A financial data API for Indian equities (NSE) that turns exchange XBRL filings into investment metrics.

## What you get

Point it at a stock symbol and Voyager returns:

**Market data** — current price, 52-week high/low, volume, RSI, moving averages (SMA/EMA), Bollinger Bands, ATR.

**Valuation** — P/E, P/B, P/S, EV/EBITDA, EV/Revenue, PEG, FCF yield, market cap, enterprise value.

**Profitability & efficiency** — net/operating margin, ROE, ROA, ROIC, asset turnover, cash & operating cash-flow ratios.

**Growth** — YoY growth for revenue, earnings, EPS, FCF, operating income, book value.

**Solvency** — debt-to-equity, debt-to-assets, interest coverage.

**Per share** — EPS, book value per share, FCF per share.

**Shareholding** — promoter, FII, DII, and public holding from the latest filing.

**Raw statements** — the actual quarterly/annual income statements, balance sheets, and cash flows pulled from NSE filings.

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

```json
{
  "symbol": "VBL",
  "period_end_date": "2026-06-30",
  "consolidated": true,
  "filing_type": "ttm",

  "current_price": 442.3,
  "rsi_14": 55.5,
  "sma_20": 430.0,
  "sma_200": 400.0,
  "bb_upper": 450.0,
  "atr_14": 8.5,
  "volume": 1200000,
  "high_52w": 500.0,
  "low_52w": 300.0,

  "market_capitalization": 1496000000000.0,
  "enterprise_value": 1499000000000.0,
  "price_to_earnings_ratio": 44.19,
  "price_to_book_ratio": 6.87,
  "price_to_sales_ratio": 6.04,
  "enterprise_value_to_ebitda_ratio": 32.47,
  "free_cash_flow_yield": 1.695,
  "peg_ratio": 2.51,

  "operating_margin": 16.0,
  "net_margin": 12.0,
  "return_on_equity": 15.66,
  "return_on_assets": 11.29,
  "return_on_invested_capital": 17.14,
  "asset_turnover": 0.9,
  "operating_cash_flow_ratio": 1.869,

  "debt_to_equity": 0.16,
  "interest_coverage": 23.56,

  "revenue_growth": 14.64,
  "earnings_growth": 18.32,
  "earnings_per_share_growth": 17.6,

  "earnings_per_share": 10.01,
  "book_value_per_share": 64.36,
  "free_cash_flow_per_share": 7.5,

  "total_debt": 5000000000.0,
  "total_equity": 217000000000.0,
  "cash_and_equivalents": 15000000000.0
}
```

---

## MCP server

`mcp_server.py` exposes the same data to MCP clients (Claude Desktop, Cursor, Claude Code, etc.) as an AI-native tool surface. It reuses the exact service layer behind the API and CLI, so every function here maps 1:1 to an endpoint or CLI command.

**Tools** (27) include:
- `get_financial_metrics` — valuation, profitability, growth, solvency (this repo's core)
- `get_financials`, `get_income_statements`, `get_balance_sheets`, `get_cash_flows`
- `pull_status` — inspect NSE data coverage in the DB
- `announcements`, `shareholdings`, `list_categories`
- Legacy utilities: `nse_financials_raw`, `nse_announcements`, `nse_annual_reports`, PDF/TOC extraction, `nse_announcements_search`
- Web screeners: `screener_fetch`, `screener_screen`, `trendlyne_fetch`, `stockscans_fetch`, `marketsmithindia_fetch`

It also ships two **resources** (`voyager://schema/{source}`, `voyager://list/{category}`) and a ready-made **prompt** (`analyze_stock`) that chains the tools into a fundamental-analysis workflow.

### Run

```bash
python mcp_server.py                                  # stdio (default, for local clients)
python mcp_server.py --transport http --port 8002     # Streamable HTTP
fastmcp run mcp_server.py:mcp --transport http --port 8002
```

The HTTP endpoint is `http://127.0.0.1:8002/mcp`. Configure via env: `MCP_TRANSPORT` (`stdio`|`http`), `MCP_HOST` (default `127.0.0.1`), `MCP_PORT` (default `8002`). Test with the Inspector: `fastmcp dev mcp_server.py`.

### Register with a client

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "voyager": {
      "command": "/path/to/conda/envs/voyager/bin/python",
      "args": ["/path/to/voyager/mcp_server.py"]
    }
  }
}
```

Cursor / other clients point at the same command, or at the HTTP endpoint above.

Notes:
- Logging goes to stderr/file, so stdout stays clean for stdio transport.
- If you expose the HTTP transport beyond localhost, put it behind auth (fastmcp supports it) — no secrets are stored in the repo.

## Technical reference

### Setup

```bash
pip install -r requirements.txt
python api.py                 # serves on 0.0.0.0:8001 (reload on in dev)
```

Configure via `.env` (copy from `.env.example`):

| Variable | Default | Notes |
|---|---|---|
| `MONGODB_URL` | *(required)* | e.g. `mongodb://root:example@localhost:27017/` or your Atlas URI |
| `MONGODB_DB_NAME` | `voyager` | |
| `VOYAGER_ADMIN_KEY` | *(unset)* | Required to use `/admin/keys`. Generate with `openssl rand -hex 32`. |
| `SENTRY_DSN` | *(unset)* | Enables Sentry error tracking when set |
| `CORS_ORIGINS` | *(unset)* | Comma-separated list; CORS middleware only added when set |
| `LOG_FILE_SINK` | *(unset)* | When set, logs also go to this file path |
| `ENVIRONMENT` | `development` | `production` disables uvicorn reload |
| `WEB_CONCURRENCY` | `2` | gunicorn workers (Render free: `1`) |

### Endpoints

Data endpoints require an API key via the `X-API-Key` (or `Authorization: Bearer`) header. Auth: 🔓 public · 🔑 any valid key · ✍️ `data:write` scope · 🛡️ `VOYAGER_ADMIN_KEY`.

| Endpoint | Auth | Description |
|---|---|---|
| `GET /` | 🔓 | Health check: `{"ok": 1}` |
| `GET /healthz` | 🔓 | Liveness probe |
| `GET /readyz` | 🔓 | Readiness probe — 503 when the DB is unreachable |
| `GET /metrics` | 🔓 | Prometheus metrics (default on; disable with `METRICS_ENABLED=false`) |
| `GET /list?category=sources` | 🔑 | Available categories: `sources`, `countries`, `industries`, `sectors`, `indices` |
| `GET /financials?symbol=VBL` | 🔑 | Latest income + balance + cash-flow merged into one doc |
| `GET /financials/income-statements` | 🔑 | Raw statement rows (also `balance-sheets`, `cash-flows`) |
| `POST /pull?symbol=VBL&filing_type=quarterly` | ✍️ | Submit async pull job → `202` with `job_id` (see [Async pulls](#async-pulls)) |
| `GET /pull?symbol=VBL` | 🔑 | Pull history, record counts, date coverage per collection |
| `GET /pull/jobs/{job_id}` | ✍️ | Poll status of a pull job |
| `GET /pull/jobs?limit=20` | ✍️ | Recent pull jobs |
| `GET /financial-metrics?symbol=VBL` | 🔑 | Computed metrics (this repo's core) |
| `GET /announcements?symbol=VBL&market=equities` | 🔑 | Corporate announcements (`equities` or `sme`) |
| `GET /shareholdings?symbol=VBL` | 🔑 | Latest promoter / FII / DII / public holding pattern |
| `GET /admin/keys` | 🛡️ | List API keys (prefixes only — hashes never returned) |
| `POST /admin/keys` | 🛡️ | Create a key; body `{name, owner?, scopes?, rpm?, expires_in_days?}` |
| `DELETE /admin/keys/{prefix}` | 🛡️ | Revoke a key |
| `POST /admin/keys/{prefix}/enable` | 🛡️ | Re-enable a revoked key |
| `GET /funds`, `/macro`, `/news` | 🔑 | Not yet implemented |

All endpoints use `country=in` and `source=nse` (others return `501`).

### API keys

Service keys are created with `/admin/keys` (guarded by `VOYAGER_ADMIN_KEY`). The raw key is returned **once**; only its SHA-256 hash and 12-char prefix are stored.

```bash
# create a read-only key for your main app
curl -X POST http://localhost:8001/admin/keys \
  -H "X-Voyager-Admin-Key: $VOYAGER_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "main-app", "owner": "your-service", "scopes": ["data:read"], "rpm": 60}'
```

Scopes: `data:read` (all reads) and `data:write` (submit/list pull jobs). Optional `rpm` rate cap (default 60 req/min, enforced with a fixed-window counter in Mongo) and `expires_in_days`.

### Async pulls

`POST /pull` returns `202 Accepted` with a `job_id` — the XBRL download/parse runs in the background (avoids Render's 60s proxy timeout). Poll `GET /pull/jobs/{job_id}` until `status` is `done` or `failed`.

```bash
curl -X POST -H "X-API-Key: $VOYAGER_API_KEY" \
  "http://localhost:8001/pull?symbol=VBL&filing_type=quarterly"
# {"job_id": "...", "status": "queued", "status_url": "/pull/jobs/..."}

curl -H "X-API-Key: $VOYAGER_API_KEY" "http://localhost:8001/pull/jobs/$JOB_ID"
```

Concurrency is capped (default `MAX_CONCURRENT_PULLS=2`, one active pull per symbol) and stale jobs are reaped on startup. Pulls mutate the database — that's why they need `data:write`.

### Remote client CLI

`client/` is a standalone Typer CLI that talks to any deployed Voyager over HTTPS. Install and use it from your main app or laptop:

```bash
pip install -r client/requirements.txt

export VOYAGER_BASE_URL=https://voyager.onrender.com
export VOYAGER_API_KEY=vgr_...          # created via `keys create`

python -m client ping
python -m client metrics VBL --filing-type ttm
python -m client financials VBL --all-fields
python -m client statements VBL income --limit 4
python -m client announcements VBL
python -m client shareholdings VBL
python -m client list-categories
python -m client pull VBL --watch        # submit + poll until done (needs data:write)
python -m client pull-jobs
python -m client pull-status VBL
```

Key management needs `VOYAGER_ADMIN_KEY`:

```bash
python -m client keys create "main-app" --scopes data:read --rpm 120
python -m client keys list-keys
python -m client keys revoke vgr_abCdEfGh
python -m client keys enable vgr_abCdEfGh
```

### Deployment (Render)

This repo ships a `render.yaml` blueprint for Render. The API runs on **Read Replicas** pattern: **Render serves reads**; **pulls run locally** (`scripts/cli.py --profile local pull ...` or the MCP/CLI tooling) and write into the same Atlas database. This keeps the Render instance cheap and stateless.

1. **MongoDB Atlas** — create a free M0 cluster, add a DB user, get the connection string (Driver: Python). Add the app's server IP (or `0.0.0.0/0`) to Network Access.
2. **Render** — New + → Blueprint, select this repo (it auto-detects `render.yaml`), or create a Web Service manually (Docker, `./Dockerfile`, plan: free, region near your users).
3. **Secrets** — on the Render Dashboard set:
   - `MONGODB_URL` = your Atlas URI (e.g. `mongodb+srv://user:pass@cluster.mongodb.net/`)
   - `VOYAGER_ADMIN_KEY` = `openssl rand -hex 32`
   - optionally `SENTRY_DSN`, `CORS_ORIGINS`
4. **Deploy** — Render builds and starts gunicorn; the health check hits `/healthz`. Verify with `curl https://<service>.onrender.com/readyz` (should return `{"ok": true}`).
5. **Create a key for your main app** (see [API keys](#api-keys)), then put `VOYAGER_BASE_URL` + `VOYAGER_API_KEY` in your app's config.
6. **Load data** — run pulls locally: `python scripts/cli.py --profile local pull VBL --filing-type quarterly` (writes into Atlas; see `profiles/atlas.env.example` for the alternate profile).

#### Monitoring

- **Sentry** — set `SENTRY_DSN` and unhandled exceptions report automatically (environment-gated; skip for dev).
- **Grafana Cloud** — add a Prometheus data source, then scrape `https://<service>.onrender.com/metrics`. Key metrics: `http_requests_total` and `http_request_duration_seconds` (both labeled by method/route/status), plus the standard Python/process collectors (`python_info`, `process_*`). Health probes already expose `/readyz` for uptime alerts.

### Key parameters

- `consolidated` — `true` (default) for consolidated statements, `false` for standalone, `null` for both.
- `filing_type` — `quarterly`, `annual`, or `ttm`.
- `refresh` — on `POST /pull`, re-downloads and re-parses XBRL already in the DB.
- `limit` — on statement endpoints, number of rows to return (0 = all).
- `all_fields` — return every stored field instead of only priority metrics.

### How metrics are computed

- **TTM** sums the latest 4 quarterly income/cash-flow records and compares against the prior 4-quarter window for growth. Balance-sheet metrics use the latest quarter's point-in-time values.
- **FCF** ≈ operating cash flow (capex isn't in the filings), so `free_cash_flow_yield` = OCF ÷ market cap.
- **PEG** uses 3Y EPS CAGR when positive, otherwise 1Y EPS growth.
- **EV/EBITDA** proxies EBITDA with EBIT (no depreciation data).
- Metrics that need data Voyager doesn't collect (COGS, current assets, dividends) return `null`.

### Notes

- Financial endpoints are India/NSE only today; the codebase also contains scrapers for web screeners (Screener, Trendlyne, StockScans), news/blog sources, and market data APIs, exposed via the `cli.py` tooling.

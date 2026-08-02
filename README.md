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
curl "http://localhost:8001/financial-metrics?symbol=VBL&filing_type=ttm"
```

`filing_type` can be `quarterly`, `annual`, or `ttm` (trailing twelve months). Full docs at `http://localhost:8001/docs`.

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

## Technical reference

### Setup

```bash
pip install -r requirements.txt
python api.py                 # serves on 0.0.0.0:8001
```

Configure via `.env`: `MONGODB_URL` (default `mongodb://root:example@localhost:27017/`), `MONGODB_DB_NAME` (default `relativity`).

### Endpoints

All endpoints use `country=in` and `source=nse` (others return `501`).

| Endpoint | Description |
|---|---|
| `GET /` | Health check: `{"ok": 1}` |
| `GET /list?category=sources` | Available categories: `sources`, `countries`, `industries`, `sectors`, `indices` |
| `GET /financials?symbol=VBL` | Latest income + balance + cash-flow merged into one doc |
| `GET /financials/income-statements` | Raw statement rows (also `balance-sheets`, `cash-flows`) |
| `POST /pull?symbol=VBL&filing_type=quarterly` | Pull & parse XBRL from NSE into the DB |
| `GET /pull?symbol=VBL` | Pull history, record counts, date coverage per collection |
| `GET /financial-metrics?symbol=VBL` | Computed metrics (this repo's core) |
| `GET /announcements?symbol=VBL&market=equities` | Corporate announcements (`equities` or `sme`) |
| `GET /shareholdings?symbol=VBL` | Latest promoter / FII / DII / public holding pattern |
| `GET /funds`, `/macro`, `/news` | Not yet implemented |

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

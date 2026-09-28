# Eval 01 — `/financial-metrics` accuracy & quality

**Endpoint under test:** `GET {VOYAGER_BASE_URL}/financial-metrics`
**What it answers:** how trustworthy is each field Voyager returns, where does it drift from published truth, and which fields are quietly wrong rather than merely missing.
**Type:** read-only, black-box, web-verified. No code changes. No DB writes. No `POST /pull`.

---

## 0. Run contract

| Item | Value |
|---|---|
| Base URL | `$VOYAGER_BASE_URL`, default `https://voyager-1hpq.onrender.com` |
| Auth | header `X-API-Key: $VOYAGER_API_KEY` — never echo the key into any artifact or report |
| Method | `GET` only. `POST /pull`, `/documents/parse`, `/sentiment/*` are forbidden in this eval |
| Default params | `filing_type=ttm`, `consolidated=true`; also run `filing_type=quarterly` for the basis checks (D4) |
| Report | `evals/runs/<YYYY-MM-DD_HHMM>_financial-metrics.md` |
| Artifacts | `evals/runs/<YYYY-MM-DD_HHMM>_financial-metrics.json`, `..._voyager_raw.json` |
| Committed | **No.** `evals/runs/` is gitignored. Only the report the user asks for gets shown |
| Output style | tables and numbers first. Insight lines are keywords, ≤12 words, never sentences |

---

## 1. Preflight

1. `GET /readyz` — must be `{"ok": true}`. If not, stop and report `BLOCKED: db unreachable`.
2. `GET /` — `{"ok": 1}`. Confirms the deployed build is live.
3. Record `VOYAGER_BASE_URL`, the API key **prefix only** (first 8 chars), the run timestamp (UTC), and the host's current date. Prices and filings are as-of this moment.
4. `GET /list?category=sources` — record valid `source` values. Only `nse` and `sec` are in scope.
5. If `evals/runs/` already holds a previous `*_financial-metrics.json`, load it: the report must include a run-over-run delta table (§6.4).

---

## 2. Universe

### 2.1 Curated core (regression baseline — always run, all of them)

| Symbol | Source | Why it is in the baseline |
|---|---|---|
| `RELIANCE` | nse | Large cap, consolidated, full 4-quarter TTM window, high other-income share (stresses operating margin) |
| `TCS` | nse | Large cap, asset-light, near-zero debt, large cash pile (stresses EV and net cash) |
| `INFY` | nse | Large cap, clean comparability, high DSO |
| `HDFCBANK` | nse | Bank/NBFC — balance-sheet-dominant, conventional ratios are meaningless, borrowings structure differs |
| `NEULANDLAB` | nse | Mid cap, documented TTM/fallback path |
| `SKY` / Skygold | nse | Documented carry-forward + XBRL-tag-trust case (debt/equity, cash) |
| `AAPL` | sec | US large cap, clean 10-K/10-Q, diluted-vs-basic EPS gap |
| `LEN` | sec | US mid cap, documented 10-K/10-Q parse, seasonal (tests YoY quarter matching) |
| `MSFT` | sec | US mega cap, TTM smoothing, negative-equity-adjacent distortions |

If a symbol has never been pulled, record it as `NO_DATA` and move on — do **not** pull it. `NO_DATA` counts against D7 coverage, not D1 accuracy.

### 2.2 Agent-picked edge cases (3–5 fresh symbols per run)

Rotate these; log the pick and the reason in the report so runs stay interpretable.

| Edge case | What it breaks if the code is wrong |
|---|---|
| Loss-making (negative EPS TTM) | `price_to_earnings_ratio` and `peg_ratio` must be `null`, never 0 or negative garbage |
| Listed < 8 quarters ago | TTM window incomplete → `free_cash_flow_source` fallback, `_ttm_window` degradation |
| Zero borrowings | `debt_to_equity` = 0 (not null), `interest_coverage` = null, ROIC denominator = equity only |
| Finance costs = 0 (NBFC/cash-rich) | `interest_coverage` null; ROIC tax-rate clamp path |
| Very high debt / negative net worth | EV sign, invested capital sign, ROE sign |
| SME / `sme` market segment | Missing price feed, missing technicals, `price_data: unavailable` |
| Currency-mismatch risk (US symbol, rupee assumption) | unit contamination in market cap |
| Multiple share classes / tiny float | `shares_outstanding` fallback via `compute_shares_outstanding` |

---

## 3. Ground truth collection

### 3.1 Source priority

**US (`source=sec`)** — 1. SEC EDGAR company facts / the 10-K & 10-Q themselves (this is the filing, not a mirror) · 2. stockanalysis.com · 3. macrotrends.net · 4. finviz.com · 5. wisesheets, gurufocus, companiesmarketcap.

**India (`source=nse`)** — 1. NSE corporate filing XBRL / the annual report PDF · 2. screener.in · 3. trendlyne.com · 4. marketsmithindia · 5. tickertape, BSE India (use these to resolve consolidated-vs-standalone) · 6. moneycontrol.

Rules:
- **≥2 independent sources per metric.** One source only → verdict `single-sourced`, counted separately, never a clean pass.
- Every truth value carries `source_url` + `as_of` date in the JSON artifact.
- Prefer the **filing** over aggregators for raw statement items; aggregators for ratios, where the filing has no ratio.

### 3.2 The two problems that break naive comparison

**a) Consolidated vs standalone (India).** Screener's ratio page is consolidated by default; many pages are standalone. If the reference does not state its basis, mark the row `basis-ambiguous` and list it in the definition-divergence table. Never score it as a Voyager error without confirming the basis.

**b) Live price drift.** Voyager computes price-derived fields from a *live* price; screener/finviz compute theirs from the *last close*. Normalise before comparing:

```
normalised_ref = ref_ratio × (voyager_current_price / ref_price)
```

Apply to `price_to_earnings_ratio`, `price_to_book_ratio`, `price_to_sales_ratio`, `enterprise_value_to_ebitda_ratio`, `enterprise_value_to_revenue_ratio`, `market_capitalization`, `enterprise_value`, `peg_ratio`. If the reference price is unavailable, mark the row `price-drift-affected` and exclude it from the score. Record `current_price` at the **start and end** of the run; if it moved >1%, re-derive every price-derived field from the start-of-run price before scoring.

### 3.3 Reference worksheet (build one row per Voyager field)

| symbol | source | metric | voyager_value | unit | ref_value | ref_unit | ref_source_url | as_of | normalised | verdict |
|---|---|---|---|---|---|---|---|---|---|---|

`verdict` ∈ `pass` · `near` · `fail` · `def-div` · `contested` · `unverified` · `price-drift` · `basis-ambiguous` · `single-sourced`.

### 3.4 Conflict resolution

1. Compute pairwise agreement between all sources for the metric.
2. **Majority vote**: the value ≥2 independent sources agree on within tolerance wins.
3. Record every dissenting value in the JSON artifact (`dissent` array) and in the report's conflict table.
4. No 2/3 majority and spread > tolerance → `contested`. Excluded from the score, listed with the full spread. Contested metrics are a *reference* problem, not a Voyager failure.
5. Never invent, interpolate, or "reason about" a truth value. If it cannot be found and cited, it is `unverified`.

---

## 4. Dimensions

Each dimension scores 0–100. Composite weights in §5.

### D1 — Absolute value accuracy · weight 30

Every field with a findable public counterpart, compared after §3.2 normalisation.

- Per-metric: `pass` rate, `near` rate, `fail` rate, **MAPE**, **signed bias** (mean of Δ% — bias separates systematic definition gaps from random noise), and worst offender symbol.
- Report bias, not just error. A consistent −3% on P/E is a definition choice (basic vs diluted EPS), not a bug. A +40% swing on one symbol is a bug.
- `unverified` fields are reported as a coverage number, never silently dropped.

### D2 — Internal consistency / identities · weight 20

Computed entirely from the Voyager payload plus one external price. No reference lookup needed. Every violated identity is a hard fail.

| # | Identity | Tolerance |
|---|---|---|
| I1 | `price_to_earnings_ratio` × `earnings_per_share` = `current_price` | 1% |
| I2 | `price_to_book_ratio` × `book_value_per_share` = `current_price` | 1% |
| I3 | `price_to_sales_ratio` × (TTM revenue / shares) = `current_price` | 2% |
| I4 | `enterprise_value` = `market_capitalization` + `total_debt` − `cash_and_equivalents` | 0.5% |
| I5 | `enterprise_value_to_ebitda_ratio` = `enterprise_value` / (EBIT + D&A) | 2% |
| I6 | ROE ≈ `net_margin` × `asset_turnover` × (assets/equity) | 3pp |
| I7 | `net_margin` = TTM PAT / TTM revenue (recover both from `/financials`) | 0.5pp |
| I8 | `debt_to_equity` = `total_debt` / `total_equity` | 1% |
| I9 | `payout_ratio` × TTM PAT = \|dividends paid\| | 2% |
| I10 | `free_cash_flow_per_share` × shares = OCF − capex, matching `free_cash_flow_source` | 2% |
| I11 | `days_receivable_outstanding` = receivables / revenue × 365 | 2 days |
| I12 | `days_payable_outstanding` = payables / COGS(or revenue) × 365 | 2 days |
| I13 | `days_inventory_outstanding` = 365 / `inventory_turnover` | 0.5 days |
| I14 | `market_capitalization` = `current_price` × shares (shares from `/financials` or the reference) | 2% |
| I15 | `interest_coverage` = EBIT / finance costs; null iff finance costs == 0 | 2% |

Flag every identity that is *unverifiable* because an input is null, and say so — a null is not a pass.

### D3 — Growth metric correctness · weight 10

- Recompute from `/financials` statement rows: `revenue_growth_yoy` = latest quarter vs the same quarter one year earlier; `revenue_growth_qoq` = vs the immediately preceding quarter. Both must match the payload.
- **TTM-window audit (the important one).** Confirm `filing_type=ttm` flows really are the sum of 4 distinct consecutive quarters, not 3, not 5, not the same quarter twice. Report the actual quarter list per symbol.
- Detect the silent fallback: when `filing_type=ttm` is requested but fewer than 4 quarters are stored, flows degrade to the latest single quarter **while the response still says `filing_type: ttm`**. Every such symbol is a `basis-mismatch` finding with the quarter count stated. This is a consumer-visible lie about the basis.
- `revenue_growth` (TTM vs prior TTM) and `revenue_growth_yoy` (quarter vs quarter) are different quantities. Report both; if the payload ever makes them numerically identical, that is a finding.
- Growth values are in **percent** (e.g. `12.4` = 12.4%). A value in `0.124` form is a unit bug.

### D4 — Basis & semantics correctness · weight 10

| Check | Pass condition |
|---|---|
| D4.1 | `last_quarter_end_date` equals the newest `period_end_date` in `/financials` |
| D4.2 | `last_quarter_end_date` equals the **latest actually filed** quarter per the exchange/EDGAR (freshness is scored in D7; here it is a *labelling* check) |
| D4.3 | `consolidated` echoes the request; the payload's numbers match the requested basis in `/financials` |
| D4.4 | `filing_type` echoes the request and the numbers are actually on that basis |
| D4.5 | TTM flow fields are 4-quarter sums; point-in-time fields (current_ratio, quick_ratio, debt_to_equity, ROE/ROA denominators, inventory_turnover denominator) are from the latest period |
| D4.6 | `last_annual_end_date` matches the company's actual fiscal year end (Mar / Dec / etc.), not a hardcoded December |
| D4.7 | Income-statement `revenue_from_operations` is used, not total income — a company with large other income will diverge from aggregators that use total income; confirm which the payload used and record it |

### D5 — Null & zero discipline · weight 10

| Check | Pass condition |
|---|---|
| D5.1 | Missing upstream data → `null`, never `0` |
| D5.2 | `price_to_earnings_ratio` is null when TTM EPS ≤ 0; `peg_ratio` null when growth ≤ 0 |
| D5.3 | `interest_coverage` null when finance costs == 0 |
| D5.4 | `free_cash_flow_source` == `operating_cash_flow_capex_absent` ⟹ `free_cash_flow_per_share` equals OCF/share, and this is disclosed, not hidden |
| D5.5 | `ebitda_margin` is null when D&A is absent, not EBIT/revenue relabelled |
| D5.6 | No fabricated fallbacks: count symbols where a degraded path produced a plausible-looking number |
| D5.7 | Sign conventions: capex magnitude positive in FCF, dividends negative in the statement but positive in `payout_ratio`, borrowings split current/non-current |
| D5.8 | **No zeroed values from rounding.** `_round2` collapses e.g. `0.004` → `0.0`. Count fields that arrive as `0.0` or `-0.0` and could plausibly have been non-zero. Report the count and the affected fields. |

Null rate is a first-class report column: `n_null / n_total` per field, per market.

### D6 — Scale, unit & currency sanity · weight 10

| Check | Pass condition |
|---|---|
| D6.1 | Margin/return/growth fields are percent (×100), ratios are plain multiples, days are days. Compare magnitudes against the reference and call out any field that is off by exactly 100× or 0.01× — those are unit bugs, not accuracy misses |
| D6.2 | `market_capitalization` magnitude matches (price × absolute share count), not price × shares-in-crore. Cross-check against screener's ₹ crore / finviz's raw figure with the conversion stated in the report |
| D6.3 | Currency: NSE symbols in ₹, SEC symbols in `$`. A ₹ figure on a `source=sec` symbol is a hard fail |
| D6.4 | `total_debt`, `total_equity`, `cash_and_equivalents` on the same scale as each other and as `market_capitalization` |
| D6.5 | Statement line items in `/financials` are on the filing's own scale (₹ raw, not thousands/lakhs/crores) and the eval's arithmetic back to the ratios uses the stated scale |
| D6.6 | `earnings_per_share` scale is per-share, not total; `book_value_per_share` likewise |

### D7 — Freshness & coverage · weight 5

| Check | Pass condition |
|---|---|
| D7.1 | `last_quarter_end_date` within ~1 quarter of the latest filed quarter |
| D7.2 | Report the **age in days** of the newest stored period |
| D7.3 | **Carry-forward exposure.** Balance-sheet fields may be filled from a prior year-end filing (up to 380 days). For each symbol, report the true age of the balance-sheet data actually used for `current_ratio`, `quick_ratio`, `debt_to_equity`, `return_on_equity`, `return_on_assets`, `asset_turnover`, `inventory_turnover`. Age > 200 days is a finding, because those ratios are then point-in-time numbers wearing a recent date |
| D7.4 | Field coverage: how many of the ~60 expected fields are non-null per symbol |
| D7.5 | `price_data` == `live` vs `unavailable`; when unavailable, every price-derived field must be null, not stale |
| D7.6 | Cross-endpoint agreement: values recoverable from `/financials` match the ratios computed from them (join on `period_end_date`) |

### D8 — Contract & performance · weight 5

| Check | Pass condition |
|---|---|
| D8.1 | HTTP 200 for every in-scope symbol; record 4xx/5xx/timeout separately |
| D8.2 | p50 / p95 latency per call; report max and which symbol |
| D8.3 | Response is a non-empty JSON object. **An empty `{}` means "no data stored" and must be reported as `NO_DATA`, never as 60 null metrics** |
| D8.4 | Schema stability: diff the key set against `src/services/metrics.py`'s `result` dict. Report added/removed keys vs the last run |
| D8.5 | All numerics are JSON numbers, not strings; no `NaN`/`Infinity` literals (which are invalid JSON and break strict clients) |
| D8.6 | `filing_type=annual` and `consolidated=false` return sane payloads, not the quarterly numbers relabelled. Spot-check 2 symbols |
| D8.7 | `current_price` present when `price_data == "live"`; `rsi_14`, `sma_*`, `high_52w`, `low_52w` present for `nse`, and the NSE-only fields (`delivery_percentage`, `relative_strength`) absent for `sec` — that asymmetry is expected, not a bug |

---

## 5. Scoring

### 5.1 Tolerance bands

| Class | Fields | pass | near | fail |
|---|---|---|---|---|
| Exact | `symbol`, `last_quarter_end_date`, `last_annual_end_date`, `consolidated`, `filing_type`, `price_data` | exact | — | mismatch |
| Multiple | P/E, P/B, P/S, EV/EBITDA, EV/Rev, D/E, current & quick ratio, asset & inventory turnover, interest coverage, PEG | ≤2% | ≤5% | >5% |
| Percent (pp) | gross / operating / EBITDA / net margin, ROE, ROA, ROIC, payout ratio | ≤0.5pp | ≤1.5pp | >1.5pp |
| Growth (pp) | all `*_growth*` fields | ≤1.5pp | ≤4pp | >4pp |
| Days | DIO, DSO, DPO | ≤3 days | ≤8 days | >8 days |
| Per-share | EPS, BVPS, FCF/share | ≤1% | ≤3% | >3% |
| Large currency (rel) | market cap, EV, total debt, total equity, cash | ≤2% | ≤5% | >5% |

**Definition-divergence exemption.** When the reference uses a genuinely different definition (see Appendix A), the eval aligns the reference to Voyager's definition using the reference's own raw components. If alignment is impossible, the verdict is `def-div`: excluded from the accuracy score, reported in the divergence table. `def-div` is never counted as a Voyager failure — but it is a *consumer-experience* finding, because a user comparing Voyager to a screener will see a mismatch.

### 5.2 Score construction

```
field_score   = (pass + 0.5×near) / (pass + near + fail)      # unverified/def-div/contested excluded
metric_score  = mean(field_score across symbols)
dimension     = mean of its metric_scores, penalties from §5.3 applied
composite     = Σ (dimension × weight)
```

Penalty caps: any violated identity in D2 caps D2 at 40. Any fabricated non-null value in D5 caps D5 at 40. A `{}` scored as real metrics caps composite at 50 for that symbol.

### 5.3 Weights and grades

| Dimension | Weight | Grade | Composite |
|---|---|---|---|
| D1 Absolute value accuracy | 30 | A | ≥90 |
| D2 Internal consistency | 20 | B | 80–89 |
| D3 Growth correctness | 10 | C | 70–79 |
| D4 Basis & semantics | 10 | D | 60–69 |
| D5 Null & zero discipline | 10 | F | <60 |
| D6 Scale/unit/currency | 10 | | |
| D7 Freshness & coverage | 5 | | |
| D8 Contract & performance | 5 | | |

Per-symbol grade uses the same bands on that symbol's own composite.

---

## 6. Report format

Tables first. Insight lines are keywords, ≤12 words, no sentences. No paragraphs.

### 6.1 Run header

| field | value |
|---|---|
| run_id / timestamp (UTC) | |
| base_url | |
| api_key_prefix | first 8 chars only |
| filing_type(s) run | |
| symbols: curated / edge / total | |
| sources used (count + domains) | |
| truth values: sourced / contested / unverified | |
| wall clock | |

### 6.2 Dimension scorecard

| dim | weight | score | weighted | pass | near | fail | excluded | top failure keyword |
|---|---|---|---|---|---|---|---|---|

### 6.3 Per-symbol scorecard

| symbol | mkt | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | composite | grade | fails | worst offender (metric: Δ%) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

### 6.4 Run-over-run delta (if a prior run exists)

| dim / metric | prev | curr | Δ | direction |
|---|---|---|---|---|

### 6.5 Per-metric accuracy — the main table, sorted worst first

| metric | n | pass% | near% | fail% | MAPE | bias% | verdict mix | worst symbol (Δ%) |
|---|---|---|---|---|---|---|---|---|

### 6.6 Every failure, one row each

| symbol | mkt | metric | voyager | truth | Δ% / Δpp | verdict | ref source | note |
|---|---|---|---|---|---|---|---|---|

### 6.7 Definition divergence

| metric | Voyager definition | reference definition | symbols affected | avg gap | alignable? |
|---|---|---|---|---|---|

### 6.8 Source conflicts / dissent

| symbol | metric | values by source | spread | majority | excluded? |
|---|---|---|---|---|---|

### 6.9 Null & coverage

| field | n_null | n_total | null rate (nse) | null rate (sec) | expected? | degradation path |
|---|---|---|---|---|---|---|

### 6.10 Freshness & carry-forward

| symbol | latest stored period | age (days) | latest filed quarter | lag | BS data age used | carry-forward? |
|---|---|---|---|---|---|---|

### 6.11 Contract & performance

| endpoint call | status | p50 ms | p95 ms | keys | schema diff vs prev | notes |
|---|---|---|---|---|---|---|

### 6.12 Risk hypotheses (Appendix B)

| # | hypothesis | result | evidence (≤5 rows) |
|---|---|---|---|

`result` ∈ `confirmed` · `refuted` · `inconclusive`.

### 6.13 Findings — maximum 10 keyword lines

No sentences. No hedging prose. Ranked by (fails × worst Δ).

```
1. <keyword> — <metric> — <n> syms
2. ...
```

### 6.14 Artifacts

Paths of the markdown, JSON, and raw-response files, plus the exact `curl` used, so the run is reproducible.

---

## 7. JSON artifact

`evals/runs/<ts>_financial-metrics.json` — machine-readable, so runs diff cleanly.

```json
{
  "run_id": "2026-09-25-1430",
  "started_utc": "...",
  "base_url": "...",
  "api_key_prefix": "vgr_1234",
  "params": {"filing_type": ["ttm", "quarterly"], "consolidated": true},
  "symbols": [{"symbol": "TCS", "source": "nse", "kind": "curated|edge", "edge_reason": null}],
  "scores": {
    "dimensions": {"D1": 91.2, "D2": 100.0},
    "composite": 88.4,
    "grade": "B",
    "per_symbol": {"TCS": {"composite": 92.1, "grade": "A", "fails": 1}}
  },
  "metrics": {
    "price_to_earnings_ratio": {
      "n": 12, "pass": 7, "near": 4, "fail": 1,
      "unverified": 1, "def_div": 1, "contested": 0,
      "mape": 2.8, "bias": -2.6,
      "worst": {"symbol": "HDFCBANK", "voyager": 18.2, "truth": 14.9, "delta_pct": 22.1}
    }
  },
  "failures": [
    {"symbol": "HDFCBANK", "metric": "price_to_earnings_ratio", "voyager": 18.2,
     "truth": 14.9, "delta_pct": 22.1, "verdict": "fail",
     "ref_sources": [{"url": "https://screener.in/company/HDFCBANK", "value": 14.9, "as_of": "2026-09-25"}],
     "dissent": [], "note": "bank: conventional P/E not meaningful"}
  ],
  "conflicts": [
    {"symbol": "INFY", "metric": "return_on_equity",
     "values": [{"source": "screener.in", "value": 28.4}, {"source": "trendlyne", "value": 31.2}],
     "spread_pct": 9.8, "majority": 28.4, "excluded": true}
  ],
  "identities": [
    {"id": "I4", "symbol": "LEN", "status": "fail", "detail": "EV off by 3.1% - cash sign"}
  ],
  "coverage": {"earnings_per_share": {"null": 0, "total": 12}},
  "freshness": [{"symbol": "SKY", "latest_period": "2026-03-31", "age_days": 178, "bs_age_days": 178, "carry_forward": true}],
  "performance": [{"symbol": "TCS", "status": 200, "p50_ms": 820, "p95_ms": 1500, "keys": 61}],
  "hypotheses": [{"id": "H1", "result": "confirmed", "evidence": "..."}]
}
```

Keep `voyager_raw` responses verbatim in a sibling file so any number in the report is traceable to bytes the API actually returned.

---

## Appendix A — field inventory, units, and definition traps

Unit conventions: `_pct` fields are **percent** (e.g. `17.2` = 17.2%). Ratio fields are plain multiples. `current_price` and statement values are in filing currency, absolute units.

| Field | Unit | Reference counterpart | Definition trap |
|---|---|---|---|
| `symbol`, `last_quarter_end_date`, `last_annual_end_date`, `consolidated`, `filing_type`, `price_data` | meta | exchange / EDGAR filings | `last_annual_end_date` may be **inferred** from the latest quarter's month, not read from a filing |
| `current_price` | ₹ / $ | live quote | live, drifts during the run |
| `market_capitalization` | ₹ / $ | screener mcap (₹ cr), finviz | price × shares; shares from the price feed or a computed fallback. Two different sources for shares |
| `total_debt` | ₹ / $ | screener Borrowings | current + non-current borrowings only; **excludes lease liabilities** |
| `total_equity` | ₹ / $ | screener Equity | share capital + other equity; may miss reserves booked elsewhere |
| `cash_and_equivalents` | ₹ / $ | screener Cash | Voyager = cash **+ bank balances other than cash & equivalents**; screener = cash only. Expect a systematic positive gap |
| `enterprise_value` | ₹ / $ | derived | mcap + debt − cash. Any `def-div` here is downstream of the three rows above |
| `price_to_earnings_ratio` | x | screener P/E, finviz P/E | **basic** EPS, TTM sum of 4 quarters. Finviz uses diluted, forward. Expect a small systematic positive gap |
| `price_to_book_ratio` | x | screener P/B | price / BVPS |
| `price_to_sales_ratio` | x | screener "Sales" | revenue = `revenue_from_operations` (**excludes other income**). Moneycontrol-style sources use total income |
| `enterprise_value_to_ebitda_ratio` | x | screener EV/EBITDA | EBITDA = EBIT + D&A; EBIT = PBT + finance costs (finance cost added back wholesale) |
| `enterprise_value_to_revenue_ratio` | x | rarely published | Voyager-only. Internal identity I4/I5 only |
| `peg_ratio` | x | finviz PEG | PE ÷ growth-in-percent (standard). Growth basis is TTM EPS growth; finviz uses forward. Null when growth ≤ 0 |
| `gross_margin` | % | screener, finviz | falls back to (revenue − total expenses) when COGS is untagged → **definition shifts mid-pipeline** |
| `operating_margin` | % | screener OPM | Voyager's "operating profit" = PBT + finance costs, so it **includes other income**. Screener's does not. Expect a systematic positive gap for companies with large non-operating income |
| `ebitda_margin` | % | screener | null unless D&A present |
| `net_margin` | % | screener NPM | PAT / revenue-from-operations |
| `return_on_equity` | % | screener ROE | TTM PAT / latest equity; the equity denominator may be **carried forward up to 380 days** |
| `return_on_assets` | % | screener ROA | same carry-forward exposure |
| `return_on_invested_capital` | % | screener ROIC | NOPAT = EBIT × (1 − effective tax), tax clamped to 0–1; invested capital = debt + equity. Screener's ROCE/ROCE uses a different base |
| `asset_turnover` | x | screener | TTM revenue / total assets |
| `inventory_turnover` | x | screener | COGS / inventory, falls back to revenue / inventory |
| `working_capital_turnover` | x | — | Voyager-only |
| `current_ratio`, `quick_ratio` | x | screener | on a possibly **carried-forward** balance sheet |
| `days_inventory_outstanding` | days | screener | 365 / inventory turnover |
| `days_receivable_outstanding` | days | screener (derived) | receivables / **revenue** × 365, not credit sales. Will differ from any credit-sales-based source |
| `days_payable_outstanding` | days | screener | payables / COGS (or revenue) × 365 |
| `debt_to_equity` | x | screener D/E | borrowings only, computed from components rather than the (unreliable) XBRL ratio tag |
| `interest_coverage` | x | screener | EBIT / finance costs, TTM on both sides; null when finance costs == 0 |
| `payout_ratio` | % | screener payout | **cash dividends paid / PAT**, not DPS / EPS. Diverges for companies whose declaration and payment fall in different periods |
| `earnings_per_share` | ₹ / $ | screener EPS | basic, TTM; **silently degrades to the latest single quarter** when 4 quarters are missing while still labelled `ttm` |
| `book_value_per_share` | ₹ / $ | screener BV | equity / shares |
| `free_cash_flow_per_share` | ₹ / $ | screener | FCF = OCF − capex; **falls back to OCF** when capex is absent — `free_cash_flow_source` discloses which |
| `free_cash_flow_source` | enum | — | degradation flag: `operating_cash_flow_minus_capex` vs `operating_cash_flow_capex_absent` |
| `revenue_growth`, `earnings_growth`, `earnings_per_share_growth`, `book_value_growth`, `free_cash_flow_growth`, `operating_income_growth`, `ebitda_growth` | % | screener growth | TTM vs prior TTM |
| `*_qoq` | % | — | latest quarter vs immediately preceding quarter |
| `*_yoy` | % | — | latest quarter vs same quarter last year |
| `rsi_14`, `sma_20/50/200`, `ema_20`, `bb_upper/middle/lower`, `atr_14`, `volume`, `avg_volume_10d`, `avg_volume_3m`, `high_52w`, `low_52w`, `change_pct`, `volume_ratio` | mixed | market data | Technical, not financial. Presence + scale sanity only |
| `delivery_percentage`, `relative_strength` | % / mixed | NSE only | Expected **absent** for `source=sec` |

## Appendix B — risk hypotheses to test each run

These are known structural risks in the current implementation. Each run states confirmed / refuted / inconclusive with evidence. Do not assume they hold — that is the point.

| # | Hypothesis | How to test |
|---|---|---|
| H1 | `filing_type=ttm` silently returns latest-quarter values when 4 quarters are missing, while still reporting `filing_type: ttm` | Count stored quarters per symbol from `/financials`; if < 4, compare TTM vs `filing_type=quarterly` payloads |
| H2 | Balance-sheet denominators are carried forward from an older year-end, so `current_ratio`, `quick_ratio`, `debt_to_equity`, ROE, ROA, `asset_turnover` reflect stale stock | Per-symbol BS data age (§6.10) and divergence vs a source using the latest filing |
| H3 | `operating_margin` is systematically higher than the reference because other income is included | Signed bias of `operating_margin` across all NSE symbols; correlate the gap with other income |
| H4 | `cash_and_equivalents` is systematically higher than the reference because bank balances are added | Signed bias of `cash_and_equivalents`; knock-on bias in EV, EV/EBITDA, EV/Rev |
| H5 | `price_to_earnings_ratio` is systematically higher because EPS is basic, not diluted | Signed bias vs a diluted-EPS source; magnitude should shrink for companies with large options/convertibles |
| H6 | `free_cash_flow_per_share` equals OCF/share for a non-trivial share of symbols | Count `free_cash_flow_source == "operating_cash_flow_capex_absent"` |
| H7 | `payout_ratio` diverges from DPS/EPS-based references | Signed bias; correlate the gap with the dividend declaration-to-payment lag |
| H8 | `_round2` zeroes small but meaningful values (PEG, thin ratios) | Count `0.0` / `-0.0` values per field; list the fields |
| H9 | `last_annual_end_date` is inferred and wrong for non-December fiscal year ends | Compare against the company's actual fiscal year end for ≥2 Indian symbols |
| H10 | The docs' sample response shows `return_on_equity: 0.17` (fraction) while the code returns percent | Check `docs/agent_endpoints.md` §5 against a live response; a consumer following the docs will misread the field. Report as a **contract** finding under D8 |
| H11 | An empty `{}` response (no stored data) is indistinguishable from a symbol with no metrics | Call a symbol that has never been pulled; record the exact response |
| H12 | Unit contamination: a 100× or 0.01× error on any field | Scan for exact 100×/0.01× ratios between Voyager and truth |

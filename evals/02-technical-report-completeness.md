# Technical report completeness eval

**Endpoint under test:** `GET {VOYAGER_BASE_URL}/technicals`
**What it answers:** does the 61-section technical report actually deliver every section the professional layout promises, is each delivered section backed by real data, and does every degraded section say *why* rather than silently going empty?

| | |
|---|---|
| Auth | header `X-API-Key: $VOYAGER_API_KEY` — never echo the key into any artifact or report |
| Params | `symbol` (primary + 2 cross-check symbols), `timeframes=daily,weekly,monthly,intraday`, `source=nse` |
| Output | `evals/runs/<YYYY-MM-DD_HHMM>_technicals.json` (+ verbatim `..._voyager_raw.json`) |
| Rule | **Read-only.** No POSTs, no DB writes. |

## 1. Preflight

1. `GET /healthz` → `{"ok": true}`; record `VOYAGER_BASE_URL`, key prefix (first 8 chars only), run timestamp (UTC).
2. Fetch `/technicals?symbol=<SYM>&timeframes=daily,weekly,monthly,intraday` for the primary symbol. Persist the raw JSON verbatim before any analysis.

## 2. The 61-section contract

The professional layout demands these 61 sections (same ordering as `REPORT_SECTIONS` in `src/services/technical_report.py`):

```
executive_summary, asset_overview, current_price_market_data,
multi_timeframe_price_analysis, price_history, trend_analysis,
market_structure,
support_resistance, supply_demand_zones, price_action_analysis,
candlestick_analysis, chart_pattern_analysis, breakout_breakdown,
moving_averages, momentum_analysis, rsi_analysis, macd_analysis,
stochastic_analysis, adx_analysis, cci_analysis, williams_r_analysis,
volume_analysis, volume_profile, vwap_analysis,
obv_accumulation_distribution, volatility_analysis, atr_analysis,
bollinger_bands_analysis, historical_volatility, relative_strength,
fibonacci_analysis, ichimoku_analysis, gap_analysis, market_regime,
trend_vs_range, bullish_bearish_signals, indicator_confluence,
divergence_analysis, short_term_setup, medium_term_setup,
long_term_structure, entry_zones, exit_zones, stop_loss,
take_profit_targets, risk_reward, invalidation_levels,
breakout_scenarios, breakdown_scenarios, bullish_scenario,
bearish_scenario, neutral_scenario, technical_signal_summary,
indicator_dashboard, mtf_signal_matrix, key_technical_levels,
upcoming_catalysts, historical_pattern_comparison,
technical_risk_factors, overall_assessment, data_sources_methodology
```

## 3. Dimensions

### 3.1 Coverage (weight 30)
For each symbol: `sections_count == 61`, section names exactly match the contract (no extras, no renames). Score = matched / 61.

### 3.2 Support rate (weight 30)
Count sections with `status == "ok"` vs `"unsupported"`. Every unsupported section **must** carry a non-empty `reason`. An unsupported section *without* a reason is a hard fail. Report the per-section support matrix as a table:

| section | status | reason (if unsupported) | spot-check |
|---|---|---|---|

### 3.3 Data-traceability spot checks (weight 25)
For each of these sections, verify the numbers are internally consistent — recomputable from *other sections* of the same response or from `/history`:

| check | expected identity | verdict |
|---|---|---|
| `current_price_market_data` vs `/history` last close | Δ ≤ 0.5% (intraday drift tolerated) | |
| `moving_averages.sma_50` vs SMA-50 recomputed from `/history` closes | Δ ≤ 0.2% | |
| `rsi_analysis.rsi_14` ∈ [0, 100] | hard bounds | |
| `williams_r_analysis` ∈ [-100, 0] | hard bounds | |
| `adx_analysis.adx_14` ∈ [0, 100]; `plus_di + minus_di` consistency | bounds | |
| `key_technical_levels.stop_loss` < reference price < first target | ordering | |
| `risk_reward` > 0 when target above stop | sign | |
| `mtf_signal_matrix` states ∈ {bullish, bearish, neutral, unavailable} | enum | |
| `fibonacci_analysis` levels strictly between swing low and swing high | ordering | |
| `support_resistance` supports < close ≤ resistances | split | |
| `support_resistance.weekly` present with daily keys preserved at top level | dual-timeframe shape | |
| `price_history.daily` closes recompute `moving_averages.sma_50` within 0.2% | Δ ≤ 0.2% | |
| `price_history.adjustment == "split+dividend adjusted"` and methodology repeats it | disclosed adjustment | |
| weekly changes: `change_pct_1w` ≈ `/history?interval=1wk` last 1 bar, no `change_pct_1d` on weekly | window semantics | |
| every change key is a multiple of its `changes_window_bars` (1/4/13/52 weekly, 1/5/21/63/252 daily) | window units | |
| candlestick hits carry `date`; a 5-candle streak yields one `three_*`/`morning_*` hit, not four | dedupe + date | |
| `market_structure` carries `pivot_count`, `confidence`, `price_vs_sma200` consistent with close vs `sma_200` | structure hygiene | |
| `technical_risk_factors`: deep-drawdown / below-SMA200 / 1y-loss symbols must NOT return the no-flags default | risk coverage | |
| scenarios: bearish targets are supports (below price), bullish targets are resistances | target side | |
| `indicator_confluence.bullish+bearish+neutral == total_signals` | count identity | hard fail on violation |
| `executive_summary.sections_supported + sections_unsupported == 61` | count identity | hard fail on violation |
| `price_history.daily` has no zero-volume bars (holiday fills dropped) | hygiene | |
| `price_history.weekly` last row carries `is_partial: true` when the week is incomplete; volume nulled before ~2y | hygiene | |
| `support_resistance` supports and resistances both non-empty when pivots exist on both sides of close (per-side cap) | no starvation | |
| `supply_demand_zones` width ≤ 10% of price; demand below close, supply above; no overlapping same-kind zones | zone sanity | |
| `volume_profile` no single bin holds a whole bar's volume (spread across [low, high]) | profile sanity | |
| RSI 47 → `neutral`, CCI −29 → `neutral`, ADX signal value carries `{adx, plus_di, minus_di}` | signal semantics | |
| `trend_analysis.trend_dimensions` separates structural vs current; `executive_summary.trend_headline` names the conflict when they disagree | trend dimensions | |
| `volume_analysis.volume_stats.unusual_days` lists the largest intraday-range outliers (e.g. a −15% wick day) | event transparency | |
| `indicator_dashboard` has `daily` / `weekly` / `monthly` indicator blocks | per-timeframe indicators | |
| `bollinger_bands_analysis` matches population-std (ddof=0) bands recomputed from `price_history.daily` | Δ ≤ 0.2% | |

Any violated identity is a hard fail regardless of weight (mirrors eval 01 §3.4).

### 3.4 Honesty (weight 15)
- Sections the data cannot support must degrade, never fabricate: cross-check `relative_strength` and `upcoming_catalysts` on a symbol with sparse announcements.
- `historical_pattern_comparison` must carry the "not a forecast" note.
- `overall_assessment` must carry the "not investment advice" note.
- No section may contain a probability or forecast number not derived from `indicator_confluence` shares.

## 4. Tolerance bands

| band | meaning |
|---|---|
| pass | Δ ≤ 0.2% or exact identity holds |
| warn | Δ ≤ 0.5% (live-price drift) |
| fail | beyond warn, or violated hard bound |

## 5. Report tables (all required)

1. **Coverage table** — 61 rows: section, status, reason, spot-check verdict.
2. **Identity table** — the §3.3 checks with expected vs actual.
3. **Degradation table** — every unsupported section across the 3 symbols with reasons; flag any section unsupported for *all* symbols (candidate data gap).
4. **Cross-symbol summary** — support rate per symbol.

## 6. JSON artifact

```json
{
  "run": {"base_url": "...", "key_prefix": "vgr_....", "as_of": "..."},
  "symbols": ["VBL", "...", "..."],
  "coverage": {"sections_expected": 61, "sections_present": 61, "score": 1.0},
  "support_rate": {"ok": 58, "unsupported": 2, "per_section": {}},
  "identities": [{"check": "...", "expected": "...", "actual": "...", "verdict": "pass"}],
  "hard_fails": [],
  "honesty_findings": [],
  "verdict": "pass | warn | fail"
}
```

## 7. Known risk hypotheses

- **Yahoo rate-limiting on Render IPs** — expect higher `unsupported` rates from the deployed instance than locally; the eval should report *where* the report was run.
- **Intraday outside market hours** — the 5m snapshot may be empty; `price_action_analysis` must degrade with a reason, not crash.
- **Announcements shape drift** — catalysts depend on the NSE announcement field names (`heading`); a scraper change silently empties section 56. The degradation table catches it.
- **Chart endpoint (parallel check)** — `GET /technicals/chart?symbol=<SYM>` must return `image/png` (not JSON, not 500); record its bytes only, no artifact.
- **Benchmark index feed** — `^NSEI` may be blocked where equities are not; `relative_strength` degrades and `technical_risk_factors` must mention it.

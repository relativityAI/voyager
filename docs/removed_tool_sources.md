# Removed Tool Source References

This file preserves the data-source names that were removed from `src/tools/` during the dead-code cleanup. These files were stubs or unused modules; their module names represent sources worth checking out in the future.

## src/tools/blog/ (Blog sources)

| Module | Source | Notes |
|---|---|---|
| `abnormal_returns.py` | Abnormal Returns | stub |
| `angry_bear.py` | Angry Bear | stub |
| `big_picture.py` | The Big Picture | stub |
| `calculated_risk.py` | Calculated Risk | stub |
| `capital_spectator.py` | Capital Spectator | stub |
| `care.py` | Care Ratings | stub |
| `chartink.py` | Chartink | stub |
| `daily_reckoning.py` | The Daily Reckoning | stub |
| `dataroma.py` | [dataroma.com](https://www.dataroma.com/m/home.php) | "not indian" |
| `etmarkets.py` | ET Markets | stub |
| `fallond.py` | Fallond Stock Picks | stub |
| `fullratio.py` | [fullratio.com](https://fullratio.com) | stub |
| `howard_lindzon.py` | Howard Lindzon | stub |
| `icra.py` | ICRA | stub |
| `investopedia.py` | Investopedia | stub |
| `longtermpick.py` | [longtermpick.com](https://longtermpick.com/) | stub |
| `morningstar.py` | Morningstar | had partial code: `https://www.morningstar.com/stocks/xnse/{symbol}/quote` and `.../valuation` |
| `reddit.py` | Reddit | stub |
| `seekingalpha.py` | Seeking Alpha | `https://seekingalpha.com/symbol/{SYMBOL}/valuation/metrics` |
| `stratechery.py` | Stratechery | stub |
| `technicals.py` | yfinance technicals (SMA/EMA/RSI/MACD/BB) | broken import (`from .utils import`), used `talib` + `yfinance` |
| `trader_feed.py` | Trader Feed | stub |
| `valueresearch.py` | Value Research | stub |
| `zero_hedge.py` | Zero Hedge | stub |

## src/tools/news/ (News sources)

| Module | Source | Notes |
|---|---|---|
| `bbc.py` | BBC | stub |
| `bloomberg.py` | Bloomberg | stub |
| `cnbc.py` | CNBC | stub |
| `crisil.py` | CRISIL | stub |
| `finviz.py` | Finviz | `https://finviz.com/quote.ashx?t={SYMBOL}&p=d` |
| `fox_business.py` | Fox Business | stub |
| `google_news.py` | Google News | stub |
| `marketscreener.py` | MarketScreener | `https://www.marketscreener.com/` |
| `marketsmojo.py` | MarketsMojo | stub |
| `marketwatch.py` | MarketWatch | stub |
| `moneycontrol.py` | Moneycontrol | stub |
| `ndtv.py` | NDTV | had full impl: `https://archives.ndtv.com/articles/{year}-{month}.html` |
| `ndtvprofit.py` | NDTV Profit | stub |
| `ny_times.py` | The New York Times | stub |
| `profitviz.py` | Profitviz | `https://profitviz.com/{SYMBOL}`; "fmp wrapper" |
| `quiverquant.py` | Quiver Quantitative | `https://www.quiverquant.com/stock/{SYMBOL}/government/` |
| `reuters.py` | Reuters | stub |
| `simply_wall_street.py` | Simply Wall St | stub |
| `the_hindu.py` | The Hindu | stub |
| `wsj.py` | Wall Street Journal | `https://www.wsj.com/news/archive/years` |
| `yf.py` | Yahoo Finance | stub |

## src/tools/form/ (Forums)

| Module | Source | Notes |
|---|---|---|
| `valuepickr.py` | ValuePickr Forum | `https://forum.valuepickr.com/search.json?q={query}` (Discourse JSON API) and `https://forum.valuepickr.com/t/{slug}/{topic_id}.json` |

## src/tools/misc/ (Misc tools)

| Module | Source | Notes |
|---|---|---|
| `sec.py` | SEC | stub |
| `tradingview.py` | TradingView | scraper idea from `https://github.com/mnwato/tradingview-scraper` |
| `youtube.py` | YouTube | full impl: `youtube_search` (YoutubeSearch) + `youtube_transcript_api` for transcripts, search URL `https://www.youtube.com/results?search_query={...}` |

## src/tools/api/ (Market-data APIs)

| Module | Source | Notes |
|---|---|---|
| `marketstack.py` | Marketstack | `https://marketstack.com/search` |

## src/tools/web_screeners/ (Web screeners)

| Module | Source | Notes |
|---|---|---|
| `tickertape.py` | Tickertape | stub |
| `tijori.py` | Tijori Finance | had full impl: search API `https://www.tijorifinance.com/api/v1/ind/company_search/?q={share}` + company page `https://www.tijorifinance.com/company/{slug}` |

## Other removed modules (outside src/tools/)

| Path | Source / Notes |
|---|---|
| `src/pipelines.py` | yfinance price pipelines + fundamentals/valuations/ratios pipelines (unused; imported non-existent `src.utils.ratios` symbols) |
| `src/utils/ratios.py` | Financial ratio calculators (margins, growth, ROE/ROA, leverage, turnover, valuations) |
| `src/utils/ocr.py` | OCR client for a LLM-powered "VoyagerOCR" service running on Colab |

## Purpose

- These files were **unreferenced** by `api.py`, `cli.py`, `src/core.py`, and all tests at cleanup time.
- Most were 1-line comment stubs (the module name is the source to check out).
- Some contained real implementations (noted above) that could be revived as reference for future integrations.

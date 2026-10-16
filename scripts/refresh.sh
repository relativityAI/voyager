#!/usr/bin/env bash
# voyager data refresh — the only write path into the DB.
#
# Cron examples (times in the server's local tz):
#   # NSE financials for the watchlist, every 6h during the filing season
#   0 */6 * * *  /path/to/voyager/.venv/bin/python -m cli ingest financials RELIANCE >> /var/log/voyager/refresh.log 2>&1
#   # SEC financials for US tickers, daily off-peak (EDGAR: 10 req/s max)
#   30 2 * * *  /path/to/voyager/.venv/bin/python -m cli ingest financials AAPL --source sec >> /var/log/voyager/refresh.log 2>&1
#   # Price history, daily after the close
#   30 21 * * * /path/to/voyager/.venv/bin/python -m cli ingest prices RELIANCE --exchange NSE --country IN >> /var/log/voyager/refresh.log 2>&1
#   # News + shareholdings
#   0 * * * *   /path/to/voyager/.venv/bin/python -m cli ingest news >> /var/log/voyager/refresh.log 2>&1
#
# Run manually:  bash scripts/refresh.sh RELIANCE [AAPL ...]
set -euo pipefail

PYTHON=${PYTHON:-python}
symbols=("$@")

# ponytail: no daemon, no locking — cron entries are staggered per symbol
# list; two runs overlapping just means the second skips existing rows.
for symbol in "${symbols[@]}"; do
    case "$symbol" in
        *.sec)
            ticker=${symbol%.sec}
            $PYTHON -m cli ingest financials "$ticker" --source sec
            ;;
        *)
            $PYTHON -m cli ingest financials "$symbol"
            $PYTHON -m cli ingest shareholdings "$symbol"
            ;;
    esac
done

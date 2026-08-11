# Local CLI tutorial (`scripts/cli.py`)

The **local CLI** is the main development tool. It talks **directly to a MongoDB database** (your local MongoDB via Docker, or MongoDB Atlas) — it does **not** go through the HTTP API. Use it for:

- pulling and parsing raw NSE XBRL filings into a database (`pull`)
- inspecting what has been pulled and is available (`pull-status`)
- computing financial metrics and printing statements from the DB
- legacy scrapers for NSE announcements, annual reports, shareholdings

---

## 1. What it is

`scripts/cli.py` is a thin launcher. It:

1. Loads environment variables from a **profile env file** (`profiles/<profile>.env`),
2. then hands off to the real Typer app in [`cli.py`](../../cli.py) at the repo root.

You can also invoke the app directly with `python cli.py ...` if the environment
variables are already set — the launcher just makes that convenient.

---

## 2. Prerequisites

- Python 3.12+ with the repo's dependencies installed (`pip install -r requirements.txt`).
- A running MongoDB for anything that touches the DB:
  ```bash
  docker compose up -d db
  ```
  (starts MongoDB on `localhost:27017`, db `voyager`).
- A profile env file (see below).

---

## 3. Configuration: profiles

The launcher maps a profile name to `profiles/<profile>.env`. Two examples ship:

**`profiles/local.env.example`** — local MongoDB via Docker:
```ini
MONGODB_URL=mongodb://root:example@localhost:27017/
MONGODB_DB_NAME=voyager
```

**`profiles/atlas.env.example`** — MongoDB Atlas (M0 free tier):
```ini
MONGODB_URL=mongodb+srv://<db_user>:<db_password>@<cluster>.<hash>.mongodb.net/?retryWrites=true&w=majority&appName=<cluster>
MONGODB_DB_NAME=voyager
```

### Setup

1. Copy an example to a real file and fill it in:
   ```bash
   cp profiles/local.env.example profiles/local.env
   # or
   cp profiles/atlas.env.example profiles/atlas.env
   ```
2. Edit the file with your real connection string. For Atlas: create a DB user
   under **Database Access**, allow your IP (or `0.0.0.0/0`) under **Network Access**,
   and paste the full connection string (URL-encode special characters in the password).
3. The env files are **gitignored** — never commit real credentials.

### Launcher flags

| Flag | Default | Description |
| --- | --- | --- |
| `--profile <name>` | `$VOYAGER_PROFILE` or `local` | Which `profiles/<name>.env` to load. |
| `--env-file <path>` | — | Load a specific env file instead of a profile. |

> `--help` (and any command help) is **not** consumed by the launcher — it's passed
> through to the Typer app, which prints its own help. So `python scripts/cli.py --help`
> works and shows the app help.

Examples:
```bash
python scripts/cli.py --profile local  pull VBL
python scripts/cli.py --profile atlas  pull VBL
python scripts/cli.py --env-file .env  pull-status VBL
```

If the profile env file does not exist, you get:
```
Profile env file not found: profiles/foo.env
Create it from profiles/foo.env.example
```

---

## 4. Global options

Provided by Typer on every invocation:

| Option | Description |
| --- | --- |
| `--install-completion` | Install shell tab-completion for the current shell. |
| `--show-completion` | Show the completion script to copy/customise. |
| `--help` | Show help and exit. |

---

## 5. Command reference

Help on any command: `python scripts/cli.py <command> --help`.

### 5.1 `ping`

Health check (mirrors `GET /`). No options.

```bash
python scripts/cli.py ping
# Voyager v0.1.0   status: ok
```

### 5.2 `version`

Print the app version. No options.

```bash
python scripts/cli.py version
# Voyager v0.1.0
```

### 5.3 `list`

List available categories (mirrors `GET /list`).

| Option | Default | Values |
| --- | --- | --- |
| `--category` | `sources` | `sources`, `countries`, `industries`, `sectors`, `indices` |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

```bash
python scripts/cli.py list
python scripts/cli.py list --category industries --country in --source nse
```

### 5.4 `pull`

Pull and parse raw NSE XBRL filings into the DB (mirrors `POST /pull`).
This is the data-ingestion workhorse.

```
Usage: cli.py pull [OPTIONS] SYMBOL
```

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol, e.g. `VBL` (**required**) |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |
| `--filing-type` | `quarterly` | `quarterly` or `annual` |
| `--refresh` / `--no-refresh` | `no-refresh` | Re-download and re-parse XBRL already in the DB |

```bash
python scripts/cli.py pull VBL
python scripts/cli.py pull VBL --filing-type annual
python scripts/cli.py pull VBL --refresh
```

Notes:
- Runs the NSE downloader + XBRL parser, writing to `income_statements`,
  `balance_sheets`, `cash_flows`, `shareholdings`, and raw collections.
- Some NSE endpoints 404 (e.g. `corp-info`); the pull still completes for the
  collections that succeeded. `pull-status` reflects the partial outcome.
- Pulling from a datacenter IP (Render) is flaky; pull locally into Atlas for
  reliable results.

### 5.5 `pull-status`

Pull history and data availability for a stock (mirrors `GET /pull`).
Shows record counts per collection plus a Financial Breakdown table
(standalone/consolidated splits by filing type).

```
Usage: cli.py pull-status [OPTIONS] SYMBOL
```

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

```bash
python scripts/cli.py pull-status VBL
```

### 5.6 `metrics`

Computed financial metrics for a stock (mirrors `GET /financial-metrics`).
Combines filings-based ratios with live price/technicals from Yahoo Finance.

```
Usage: cli.py metrics [OPTIONS] SYMBOL
```

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |
| `--consolidated` / `--no-consolidated` | `consolidated` | Consolidated vs standalone statements |
| `--filing-type` | `quarterly` | `quarterly`, `annual`, or `ttm` |

```bash
python scripts/cli.py metrics VBL
python scripts/cli.py metrics VBL --filing-type ttm
python scripts/cli.py metrics VBL --no-consolidated --filing-type annual
```

Output includes `price_data: live|unavailable`; when the live provider is
rate-limited the price-derived fields are omitted instead of erroring.

### 5.7 `announcements`

Corporate announcements for a stock (mirrors `GET /announcements`).

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |
| `--market` | `equities` | `equities` or `sme` |

```bash
python scripts/cli.py announcements VBL
python scripts/cli.py announcements VBL --market sme
```

### 5.8 `shareholdings`

Shareholding pattern for a stock (mirrors `GET /shareholdings`).

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

```bash
python scripts/cli.py shareholdings VBL
```

### 5.9 `funds`, `macro`, `news` — placeholders

These exist to mirror API endpoints that aren't implemented yet. They print a
"not yet implemented" panel. No options.

```bash
python scripts/cli.py funds
python scripts/cli.py macro
python scripts/cli.py news
```

---

## 6. `financials` group

Financial statements from the DB. Invoke without a subcommand to see help:

```bash
python scripts/cli.py financials
```

Subcommands:

| Subcommand | API mirror | Description |
| --- | --- | --- |
| `merged` | `GET /financials` | Merged income + balance + cash-flow for the latest period |
| `income` | `GET /financials/income-statements` | Income statement rows |
| `balance-sheet` | `GET /financials/balance-sheets` | Balance sheet rows |
| `cash-flow` | `GET /financials/cash-flows` | Cash flow rows |

### 6.1 `financials merged`

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |
| `--consolidated` / `--no-consolidated` | `consolidated` | Consolidated vs standalone |
| `--filing-type` | `quarterly` | `quarterly` or `annual` |
| `--all-fields` / `--no-all-fields` | `no-all-fields` | All stored fields instead of only priority metrics |

```bash
python scripts/cli.py financials merged VBL
python scripts/cli.py financials merged VBL --filing-type annual --all-fields
```

### 6.2 `financials income` / `balance-sheet` / `cash-flow`

These three share the same options. Note that **`--consolidated` is free-text
here** (not a boolean flag):

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |
| `--consolidated` | `true` | `true`, `false`, or `both` |
| `--filing-type` | `quarterly` | `quarterly` or `annual` |
| `--limit` | `4` | Max periods to show; `0` = all (integer, ≥ 0) |
| `--all-fields` / `--no-all-fields` | `no-all-fields` | Return all stored fields |

```bash
python scripts/cli.py financials income VBL --limit 8
python scripts/cli.py financials income VBL --consolidated both --filing-type annual
python scripts/cli.py financials balance-sheet VBL
python scripts/cli.py financials cash-flow VBL --limit 0
```

`--consolidated` accepts `true/false/1/0/yes/no/consolidated/standalone/both/all/any/none`.

---

## 7. `tools` group

Legacy NSE / PDF utilities that have **no API counterpart**. Invoke without a
subcommand to see help:

```bash
python scripts/cli.py tools
```

| Subcommand | Description |
| --- | --- |
| `schema` | Show the response model schema for a data source |
| `nse-financials` | Fetch and parse raw NSE financial XBRL filings |
| `nse-announcements` | Fetch raw NSE announcements |
| `nse-announcements-search` | Search announcements stored in the DB |
| `nse-announcements-extract` | Extract text content of a stored announcement PDF |
| `nse-list-annual-reports` | List annual reports for a symbol stored in the DB |
| `nse-annual-reports` | Fetch annual report metadata from NSE |
| `nse-full-download` | Full legacy NSE scrape (financials, announcements, shareholdings, annual reports) |
| `nse-shareholdings` | **Alias of `nse-full-download`** — runs the full scrape, not just shareholdings |

> ⚠️ **Quirk:** `tools nse-shareholdings` is registered as an alias of
> `tools nse-full-download`. Despite its name it runs the **whole** legacy scrape
> (financials + announcements + shareholdings + annual reports).

### 7.1 `tools schema`

```
Usage: cli.py tools schema [OPTIONS] SOURCE
```
Prints the JSON schema of the response model for a data source. Errors with
`No model found for source '<source>'` if unknown.

```bash
python scripts/cli.py tools schema nse
```

### 7.2 `tools nse-financials`

```
Usage: cli.py tools nse-financials [OPTIONS] SYMBOL
```
Fetch and parse raw NSE XBRL financials for a symbol. Prints the parsed JSON.

```bash
python scripts/cli.py tools nse-financials VBL
```

### 7.3 `tools nse-announcements`

| Option | Description |
| --- | --- |
| `SYMBOL` (arg) | NSE symbol (**required**) |
| `--save` | Also save the scraped announcements to the `nse-announcements` collection |

Without `--save`, prints the scraped results as JSON. With `--save`, inserts them
into the DB and logs `Scrape and save complete`.

```bash
python scripts/cli.py tools nse-announcements VBL
python scripts/cli.py tools nse-announcements VBL --save
```

### 7.4 `tools nse-announcements-search`

Search the `nse-announcements` collection by keyword and cutoff date.

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--keywords` | `transcript` | Case-insensitive keyword to match |
| `--cutoff-date` | `2026-01-01` | Max `sort_date` (YYYY-MM-DD) |

```bash
python scripts/cli.py tools nse-announcements-search VBL --keywords dividend
python scripts/cli.py tools nse-announcements-search VBL --keywords bonus --cutoff-date 2026-06-30
```

### 7.5 `tools nse-announcements-extract`

```
Usage: cli.py tools nse-announcements-extract [OPTIONS] PATH_OR_URL
```
Extract the text of a stored announcement PDF (looked up in the DB by
`attchmntFile`). Errors if the document isn't found.

```bash
python scripts/cli.py tools nse-announcements-extract https://nsearchives.nseindia.com/announcements/xxx.pdf
```

### 7.6 `tools nse-list-annual-reports`

```
Usage: cli.py tools nse-list-annual-reports [OPTIONS] SYMBOL
```
Lists annual-report records for a symbol stored in the `nse-annual-reports` collection.

```bash
python scripts/cli.py tools nse-list-annual-reports VBL
```

### 7.7 `tools nse-annual-reports`

| Option | Description |
| --- | --- |
| `SYMBOL` (arg) | NSE symbol (**required**) |
| `--save` | Also save the scraped metadata to the `nse-annual-reports` collection |

```bash
python scripts/cli.py tools nse-annual-reports VBL
python scripts/cli.py tools nse-annual-reports VBL --save
```

### 7.8 `tools nse-full-download` / `tools nse-shareholdings`

```
Usage: cli.py tools nse-full-download [OPTIONS] SYMBOL
```
Runs `nse-financials`, `nse-announcements`, `nse-shareholdings` (the scrape),
and `nse-annual-reports` back-to-back. Logs `Full data scrape complete`.

```bash
python scripts/cli.py tools nse-full-download VBL
```

---

## 8. Output format

Output uses [Rich](https://rich.readthedocs.io/) — panels, tables and colored text:

- `ping` → a single-line status panel.
- `list`, statements, shareholdings → Rich tables.
- `financials merged` → one panel per section (Income Statement / Balance Sheet / Cash Flow / Other).
- `metrics` → a metrics table with price-data, valuation, profitability, solvency, growth sections.
- `pull` / `pull-status` → record counts and availability breakdown.
- `tools schema` and most `tools` fetchers → raw JSON.
- Errors → a red "Voyager error" / "Unexpected error" panel, **exit code 1**.

---

## 9. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Profile env file not found: profiles/foo.env` | Create the profile env file (`cp profiles/foo.env.example profiles/foo.env`) or pass `--env-file`. **Note:** the launcher checks the env file *before* handing off, so even `--help` fails without it. |
| `ServerSelectionTimeoutError` / connection refused | Is MongoDB up? `docker compose up -d db`. |
| `DNS ConfigurationError` with Atlas | The URI has placeholders like `<cluster>.<hash>.mongodb.net` — replace them with your real Atlas connection string. |
| `Authentication failed` | Check the DB user/password and that your IP is in Atlas Network Access (or `0.0.0.0/0`). |
| Pull ends up `partial` | Some NSE endpoints 404 from certain networks (e.g. `corp-info`). Other collections still pull — check `pull-status`. |
| Financial Breakdown crashes in `pull-status` | Fixed in v0.1.x (missing `await` on the aggregate). Update to the latest commit. |
| `metrics` 500s / no price | Live quote provider (Yahoo Finance) rate-limits datacenter IPs. `price_data: unavailable` means price fields are skipped; filings metrics still return. |

---

## 10. Direct invocation (without the launcher)

If your environment variables are already set, skip the launcher:

```bash
export MONGODB_URL=mongodb://root:example@localhost:27017/
export MONGODB_DB_NAME=voyager
python cli.py pull VBL
```

This is what `scripts/cli.py` does under the hood.

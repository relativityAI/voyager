# Remote client CLI tutorial (`python -m client`)

The **remote client** is a small HTTPS client for a **deployed** Voyager API. It
does not touch MongoDB itself — every command is an authenticated HTTP call to a
base URL (e.g. the Render deployment). Use it to:

- health-check the deployment (`ping`)
- read data (`metrics`, `financials`, `statements`, `announcements`, `shareholdings`, `list-categories`)
- submit **async** data pulls on the server (`pull`, `pull-jobs`, `pull-job`)
- manage service API keys (`keys`)

It lives in the [`client/`](../../client) package and is invoked as
`python -m client`.

---

## 1. Prerequisites

- Python 3.12+ with `client/requirements.txt` installed:
  ```bash
  pip install -r client/requirements.txt
  ```
- A running Voyager API to talk to (see the Render deploy runbook in the README).
- A **service API key** (`data:read` scope) — mint one from the admin panel.

---

## 2. Configuration

Config is resolved as: **CLI flags > environment variables**.

| Flag | Env var | Purpose |
| --- | --- | --- |
| `--base-url <url>` | `VOYAGER_BASE_URL` | API base URL, e.g. `https://voyager-1hpq.onrender.com` |
| `--api-key <key>` | `VOYAGER_API_KEY` | Service API key |

These are **root** options — put them before the subcommand:

```bash
python -m client --base-url https://voyager-1hpq.onrender.com --api-key vgr_... metrics VBL
```

Or set env vars once per shell:

```bash
export VOYAGER_BASE_URL=https://voyager-1hpq.onrender.com
export VOYAGER_API_KEY="vgr_your-service-key"
python -m client metrics VBL
```

### Missing config errors

| Situation | Error |
| --- | --- |
| No base URL | `Missing base URL. Set VOYAGER_BASE_URL or pass --base-url.` (exit 1) |
| No API key | `No API key configured. Set VOYAGER_API_KEY.` |

Note: `python -m client --help` still shows help even with no config (the checks
run when a command executes).

---

## 3. Auth model in a nutshell

- **Service keys** (prefix `vgr_...`) are sent as the `X-API-Key` header.
- **Scopes**: `data:read` covers every read endpoint.
- Read commands authenticate via `X-API-Key`; **all** data endpoints are keyed
  (no anonymous reads on the deployed service).
- The API is read-only — there are no write commands to authorize. Ingestion
  runs against the `src.ingest` package; key management via the admin panel.

---

## 4. Global options

```bash
python -m client --help
```

Root options are listed above (`--base-url`, `--api-key`).
There is no `--install-completion` here (`add_completion=False`).

Top-level commands:

```
ping  metrics  financials  statements  announcements  shareholdings
list-categories
```

---

## 5. Read commands (need `data:read`)

### 5.1 `ping`

Health check. No options.

```bash
python -m client ping
# { "ok": 1 }
```

### 5.2 `metrics`

Computed financial metrics (mirrors `GET /financial-metrics`).

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--filing-type` | `quarterly` | `quarterly`, `annual`, or `ttm` |
| `--consolidated` / `--no-consolidated` | `consolidated` | Consolidated vs standalone |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

```bash
python -m client metrics VBL --filing-type ttm
python -m client metrics VBL --no-consolidated
```

Prints JSON. The response includes `price_data: "live" | "unavailable"`.

### 5.3 `financials`

Merged income + balance + cash-flow for the latest period (`GET /financials`).

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--filing-type` | `quarterly` | `quarterly` or `annual` |
| `--consolidated` / `--no-consolidated` | `consolidated` | Consolidated vs standalone |
| `--all-fields` | off | Return all stored fields |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

```bash
python -m client financials VBL --all-fields
```

### 5.4 `statements`

Raw statement rows (`GET /financials/{income-statements|balance-sheets|cash-flows}`).

```
Usage: python -m client statements [OPTIONS] SYMBOL STATEMENT
```

| Argument | Description |
| --- | --- |
| `SYMBOL` | NSE symbol (**required**) |
| `STATEMENT` | One of `income`, `balance`, `cash-flow` (**required**) |

| Option | Default | Description |
| --- | --- | --- |
| `--limit` | `4` | Max periods to show |
| `--filing-type` | `quarterly` | `quarterly` or `annual` |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

Any other statement name prints `statement must be one of: income, balance, cash-flow` (exit 1).

```bash
python -m client statements VBL income --limit 8
python -m client statements VBL balance --filing-type annual
```

### 5.5 `announcements`

Corporate announcements (`GET /announcements`).

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--market` | `equities` | `equities` or `sme` |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

```bash
python -m client announcements VBL
```

### 5.6 `shareholdings`

Shareholding pattern (`GET /shareholdings`).

| Option | Default |
| --- | --- |
| `SYMBOL` (arg) | — (**required**) |
| `--country` | `in` |
| `--source` | `nse` |

```bash
python -m client shareholdings VBL
```

### 5.7 `list-categories`

List available categories (`GET /list`).

| Option | Default | Values |
| --- | --- | --- |
| `--category` | `sources` | `sources`, `countries`, `industries`, `sectors`, `indices` |
| `--country` | `in` | country code |
| `--source` | `nse` | data source |

```bash
python -m client list-categories
python -m client list-categories --category sectors
```

---

## 6. Output & errors

- All commands print the API response as **JSON** (indented, `default=str`
  for non-JSON values).
- Any non-2xx response raises a `VoyagerError` printed in **red**
  (e.g. `401: ...` for revoked keys, `404: ...`) and exits with code 1.
- Timeout per request is 90 seconds.

---

## 7. End-to-end example

```bash
# 1. Point at the deployment
export VOYAGER_BASE_URL=https://voyager-1hpq.onrender.com

# 2. Mint a key (admin panel or operator CLI) — or reuse an existing one

# 3. Read-only queries
export VOYAGER_API_KEY="vgr_..."
python -m client ping
python -m client metrics VBL --filing-type ttm
python -m client financials VBL
python -m client statements VBL income --limit 8
```

---

## 8. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Missing base URL` | Set `VOYAGER_BASE_URL` or pass `--base-url`. |
| `No API key configured` | Set `VOYAGER_API_KEY` (or `--api-key`). |
| `401` on a key | The key was revoked or expired — re-enable or re-mint from the admin panel. |
| `500 Internal Server Error` on metrics | Usually the live price provider failed server-side. The `price_data: unavailable` field marks the degraded path (fixed in the merged v0.1.x). |
| `404` on a symbol | It was never ingested — ingest it first. |

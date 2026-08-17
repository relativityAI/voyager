# Remote client CLI tutorial (`python -m client`)

The **remote client** is a small HTTPS client for a **deployed** Voyager API. It
does not touch MongoDB itself — every command is an authenticated HTTP call to a
base URL (e.g. the Render deployment). Use it to:

- health-check the deployment (`ping`)
- read data (`metrics`, `financials`, `statements`, `announcements`, `shareholdings`, `list-categories`, `pull-status`)
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
- A **service API key** (`data:read` scope for reads, `data:read,data:write` for
  pull/job commands) — mint one with `keys create` (needs the admin key).
- The **admin key** only for `keys` commands.

---

## 2. Configuration

Config is resolved as: **CLI flags > environment variables**.

| Flag | Env var | Purpose |
| --- | --- | --- |
| `--base-url <url>` | `VOYAGER_BASE_URL` | API base URL, e.g. `https://voyager-1hpq.onrender.com` |
| `--api-key <key>` | `VOYAGER_API_KEY` | Service API key |
| `--admin-key <key>` | `VOYAGER_ADMIN_KEY` | Admin key (only needed for `keys`) |

These are **root** options — put them before the subcommand:

```bash
python -m client --base-url https://voyager-1hpq.onrender.com --api-key vgr_... metrics VBL
```

Or set env vars once per shell:

```bash
export VOYAGER_BASE_URL=https://voyager-1hpq.onrender.com
export VOYAGER_API_KEY="vgr_your-service-key"
export VOYAGER_ADMIN_KEY="your-admin-key"   # only for keys commands
python -m client metrics VBL
```

### Missing config errors

| Situation | Error |
| --- | --- |
| No base URL | `Missing base URL. Set VOYAGER_BASE_URL or pass --base-url.` (exit 1) |
| No API key (non-admin command) | `No API key configured. Set VOYAGER_API_KEY.` |
| No admin key (admin command) | `No admin key configured. Set VOYAGER_ADMIN_KEY.` |

Note: `python -m client --help` still shows help even with no config (the checks
run when a command executes).

---

## 3. Auth model in a nutshell

- **Service keys** (prefix `vgr_...`) are sent as the `X-API-Key` header.
- **Scopes**: `data:read` (all read endpoints) and `data:write`
  (`pull`, `pull-jobs`). A `data:read`-only key on a write command gets
  `403: This key requires the 'data:write' scope`.
- **Admin key** is sent as `X-Voyager-Admin-Key` and guards everything under `keys`.
- Read commands authenticate via `X-API-Key`; **all** data endpoints are keyed
  (no anonymous reads on the deployed service).

---

## 4. Global options

```bash
python -m client --help
```

Root options are listed above (`--base-url`, `--api-key`, `--admin-key`).
There is no `--install-completion` here (`add_completion=False`).

Top-level commands:

```
ping  metrics  financials  statements  announcements  shareholdings
list-categories  pull-status  pull  pull-jobs  pull-job  keys
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

### 5.8 `pull-status`

Pull history and data availability (`GET /pull`). Read-only.

| Option | Default |
| --- | --- |
| `SYMBOL` (arg) | — (**required**) |
| `--country` | `in` |
| `--source` | `nse` |

```bash
python -m client pull-status VBL
```

---

## 6. Write commands (need `data:write`)

### 6.1 `pull`

Submit an **async** pull job on the server (`POST /pull`). Requires `data:write`.

| Option | Default | Description |
| --- | --- | --- |
| `SYMBOL` (arg) | — | NSE symbol (**required**) |
| `--filing-type` | `quarterly` | `quarterly` or `annual` |
| `--refresh` | off | Re-download/re-parse XBRL already in the DB |
| `--watch` / `--no-watch` | `watch` | Poll the job until it finishes |

```bash
python -m client pull VBL --no-watch
# Submitted pull for VBL  job_id=2174b161-... status=queued

python -m client pull VBL            # watch: polls every 2s
```

Watch behavior:
- Polls `GET /pull/jobs/<job_id>` every 2 seconds, up to 120 times (~4 minutes).
- Prints the final job JSON; exit code `1` if the job `failed`.
- On timeout prints `Timed out waiting for job to finish; check 'pull-jobs'.` (exit 1).

> Because the pull runs on the server's network, it may return a `partial`
> status (some NSE endpoints 404 from a datacenter IP). For reliable full pulls,
> use the local CLI against Atlas instead.

### 6.2 `pull-jobs`

List recent pull jobs (`GET /pull/jobs`). Requires `data:write`.

| Option | Default |
| --- | --- |
| `--limit` | `20` |

Prints a table of `job_id | symbol | filing_type | status | time`.

```bash
python -m client pull-jobs --limit 10
```

### 6.3 `pull-job`

Show one pull job in full — status, timing, and the per-endpoint breakdown
(`GET /pull/jobs/{job_id}`). Requires `data:write`.

| Argument | Description |
| --- | --- |
| `JOB_ID` | Job id (from `pull-jobs` or the `pull` output) — **required** |

Prints a summary table, then the pull result: `status` (colored), record/XBRL
counts, the `endpoint_breakdown` table (numeric counts green, `no data` dim,
failures like `cookie failed` red), and per-phase timings.

```bash
python -m client pull-job 502f69b4-9930-4189-abf6-4e34a80df532
```

Use this to see *why* a pull ended `partial`/`failed` — each NSE endpoint row
shows either a record count (worked) or the failure reason.

---

## 7. `keys` group — API key management

Requires the **admin key** (`VOYAGER_ADMIN_KEY` / `--admin-key`). Invoke without
a subcommand to see help:

```bash
python -m client keys
```

Subcommands:

| Subcommand | Description |
| --- | --- |
| `create` | Create an API key. Shows the raw key once. |
| `list-keys` | List API keys (raw keys/hashes never shown). |
| `revoke` | Revoke an API key by prefix. |
| `enable` | Re-enable a revoked API key. |

### 7.1 `keys create`

```
Usage: python -m client keys create [OPTIONS] NAME
```

| Argument | Description |
| --- | --- |
| `NAME` | Key name (**required**) — positional, e.g. `"main-app"` |

| Option | Default | Description |
| --- | --- | --- |
| `--owner` | `""` | Free-text owner label |
| `--scopes` | `data:read` | Comma-separated scopes, e.g. `data:read,data:write` |
| `--rpm` | `60` | Rate limit (requests per minute) |
| `--expires-in-days` | — | Optional expiry, in days |

```bash
python -m client keys create main-app --scopes data:read,data:write --owner your-service
```

Output:
```
API key created. Store it now — it is shown only once:
vgr_xxxxxxxx...
Key details
{ ...api_key metadata... }
```

> ⚠️ The raw key is **only printed once**. Store it immediately — the API only
> stores the SHA-256 hash.

> ℹ️ `NAME` is positional; there is no `--name` option.

### 7.2 `keys list-keys`

```bash
python -m client keys list-keys
```
Prints a table of `prefix | name | owner | scopes | rpm | enabled`.

> ⚠️ The command is `list-keys`, not `keys list`.

### 7.3 `keys revoke`

```
Usage: python -m client keys revoke [OPTIONS] PREFIX
```
Revoke by key **prefix** (the leading characters shown in `list-keys`).

```bash
python -m client keys revoke vgr_abc
```

### 7.4 `keys enable`

```
Usage: python -m client keys enable [OPTIONS] PREFIX
```
Re-enable a revoked key.

```bash
python -m client keys enable vgr_abc
```

---

## 8. Output & errors

- All read commands print the API response as **JSON** (indented, `default=str`
  for non-JSON values).
- `pull` prints a green status line; `pull-job`, `pull-jobs`, `keys list-keys` print Rich tables.
- Any non-2xx response raises a `VoyagerError` printed in **red**
  (e.g. `403: This key requires the 'data:write' scope`, `401: ...` for revoked
  keys, `404: ...`) and exits with code 1.
- Timeout per request is 90 seconds.

---

## 9. End-to-end example

```bash
# 1. Point at the deployment
export VOYAGER_BASE_URL=https://voyager-1hpq.onrender.com

# 2. Mint a key (admin only) — or reuse an existing one
export VOYAGER_ADMIN_KEY="your-admin-key"
python -m client keys create main-app --scopes data:read,data:write
python -m client keys list-keys

# 3. Read-only queries
export VOYAGER_API_KEY="vgr_..."
python -m client ping
python -m client metrics VBL --filing-type ttm
python -m client financials VBL
python -m client statements VBL income --limit 8
python -m client pull-status VBL

# 4. Submit a server-side pull
python -m client pull VBL --no-watch
python -m client pull-jobs
python -m client pull-job <job_id>   # inspect status + endpoint breakdown

# 5. Revoke a leaked/old key
python -m client keys revoke vgr_abc
```

---

## 10. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Missing base URL` | Set `VOYAGER_BASE_URL` or pass `--base-url`. |
| `No API key configured` | Set `VOYAGER_API_KEY` (or `--api-key`). |
| `No admin key configured` | `keys` commands need `VOYAGER_ADMIN_KEY`. |
| `401` on a key | The key was revoked or expired — check `keys list-keys` and `keys enable` / `keys create`. |
| `403: ... 'data:write' scope` | The key only has `data:read`. Create/rotate with `data:read,data:write` for pull commands. |
| `500 Internal Server Error` on metrics | Usually the live price provider failed server-side. The `price_data: unavailable` field marks the degraded path (fixed in the merged v0.1.x). |
| Pull returns `partial` | The server's IP got NSE 404s (e.g. `corp-info`). Use the local CLI against Atlas for full pulls. |

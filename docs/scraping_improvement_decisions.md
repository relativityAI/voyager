# Scraping Improvement — Decision Log

This file records every decision made while hardening and modularizing Voyager's
scraping pipeline, and the reasoning behind it. It exists so the change history
can be reviewed later without re-deriving the context.

Context source: three research reports analyzing how NSE scraping projects
(`nse-bse-api`, `pnsea`, `stealthkit`) defeat NSE's WAF, plus a deep dive into
Voyager's existing pipeline (`src/tools/nse/client.py`, `src/services/nse.py`,
`src/utils/rate_limiter.py`, `src/utils/web.py`).

---

## D-01 — Use `curl-cffi` with TLS impersonation as the transport

**Status:** Decided (approved by user)
**Date:** 2026-08-11

**Decision:** Replace plain `requests` (urllib3 + system OpenSSL) as the NSE
HTTP transport with `curl-cffi`, using its `impersonate=` profiles to replay a
real browser's TLS/JA3 + HTTP/2 fingerprint.

**Why:**
- NSE's WAF fingerprints the TLS ClientHello (cipher suite order, extension
  order, GREASE values, ALPN, HTTP/2 SETTINGS) before any HTTP header is seen.
- `requests`/`httpx` cannot change their TLS fingerprint. `curl-cffi` is a
  binding to `curl-impersonate` (libcurl + BoringSSL) that replays a browser
  handshake byte-for-byte.
- `stealthkit`'s git history is direct evidence: header tricks + cookie priming
  on plain `requests` were insufficient; the commit that swapped to `curl_cffi`
  was the actual unlock against NSE.
- `curl-cffi` mirrors the `requests` API (`Session.get/post/...`), and its
  `Response` exposes `.status_code`, `.json()`, `.content`, so it is a drop-in
  replacement for the current code's consumption pattern.

**Trade-offs considered:**
- `httpx` HTTP/2 is already installed but does NOT impersonate TLS/JA3 — weaker,
  likely to keep getting blocked. Rejected as primary transport.
- Native dependency: acceptable; wheels exist for linux-x86_64 and it is already
  installed in the dev environment (0.15.0).

**Pin:** `curl-cffi==0.15.0` (verified present in the dev environment; latest
available is 0.16.0 — bump deliberately, don't force an unpinned upgrade).

---

## D-02 — Keep one stable per-session fingerprint (stop rotating the UA)

**Status:** Decided
**Date:** 2026-08-11

**Decision:** Set the session identity (User-Agent + impersonation profile)
exactly once per `StealthSession`; never regenerate headers between the
warm-up request and API calls.

**Why:**
- The current code (`src/tools/nse/client.py`, `_set_cookies`) calls
  `generate_fake_headers()` on every prime, producing a *new random UA per
  call*. The `nseappid`/`nsit` cookie is issued bound to the priming
  fingerprint (IP + UA + TLS); later API calls carried the same cookie under a
  *different* UA — a WAF-detectable mismatch that forces the 401 → clear →
  re-prime cycle the code already logs.
- Research (stealthkit/pnsea): the token cookie is bound to IP + fingerprint;
  don't reuse a primed session across identities, and keep UA consistent with
  the impersonation profile.

**Consequence:** header generation moves into a per-session profile; the old
`generate_fake_headers()` stays for the legacy web-screener tools only.

---

## D-03 — Cookie priming on a stable JS-light page, once per process/TTL

**Status:** Decided (user confirmed: option-chain is fine)
**Date:** 2026-08-11

**Decision:**
- Warm-up (prime) target: `https://www.nseindia.com/option-chain` (a normal
  HTML page NSE serves without JS/AJAX, which is where the WAF issues the
  session cookie), with a configured fallback list.
- Prime once per process and refresh on a TTL (default 30 min) rather than
  per-symbol.
- API-call `Referer` stays the **get-quotes page**
  (`https://www.nseindia.com/get-quotes/equity?symbol={symbol}`) so each API
  call looks like an in-page AJAX fetch of the data we actually use.

**Why:**
- `nse-bse-api` validated `/option-chain` as a priming target; `stealthkit`
  validated a deep `companies-listing` page. We keep the former as default with
  the latter as fallback.
- Voyager's product is financials/XBRL, not option-chain data — option-chain is
  used *only* as a cookie farm. The `Referer` is set to the page matching the
  endpoints we hit (get-quotes), which is what NSE's API expects.
- Per-symbol priming (current behavior) multiplies warm-up requests and churn.

---

## D-04 — Cookie persistence with a store abstraction (file → memory → mongo)

**Status:** Decided (user asked to consider remote/Render deploys)
**Date:** 2026-08-11

**Decision:** Persist the primed cookie jar through a `CookieStore` abstraction
with a fallback ladder:
1. **file** (default for local pulls): JSON jar at `data/cookies/<source>.json`
   (env-overridable via `NSE_COOKIE_PATH`).
2. **memory**: no persistence; prime once per process start. Safe default for
   ephemeral/read-only filesystems (Render).
3. **mongo** (extension point, not built now): store the jar in the existing
   Voyager DB for future remote pull workers.

Any write failure degrades gracefully to in-memory with a warning — scraping
never crashes because the FS is read-only.

**Why:** the README says pulls run locally today, but the user flagged that
pulls may run on a remote server/Render in the future, where the filesystem is
ephemeral or non-writable. A single warm-up GET per worker start is cheap
(`nse-bse-api` persists to disk; `stealthkit` re-primes per session).

---

## D-05 — Complete, coherent browser header fingerprint with two modes

**Status:** Decided
**Date:** 2026-08-11

**Decision:** Each source gets a coherent header profile:
- Common: stable UA (matches impersonation profile), `Accept-Language:
  en-US,en;q=0.5`, `Accept-Encoding: gzip, deflate, br`, `Connection:
  keep-alive`, `Upgrade-Insecure-Requests`.
- **Page-load mode** (warm-up GET): generic browser `Accept`.
- **API/fetch() mode** (data calls): `Referer` = a real page on the origin,
  `Accept: application/json`, and `sec-fetch-dest: empty`,
  `sec-fetch-mode: cors`, `sec-fetch-site: same-origin`.

**Why:** research shows WAFs distinguish "JS running inside the page" (XHR
fetch metadata + same-origin referer) from raw HTTP clients; mixing page-load
and API header sets looks wrong, and omitting `sec-fetch-*`/`Referer` is the
classic bot signal. UA + language + encoding must all be from the same browser
family.

---

## D-06 — Global token-bucket throttle with jitter (preserves speed)

**Status:** Decided
**Date:** 2026-08-11

**Decision:** New token-bucket throttle, global per source, sharing the same
budget as today (`NSE_CALLS_PER_SECOND=10`): refill 1 token per interval,
small burst capacity (3–5 tokens) so the 9-way parallel endpoint fetch doesn't
serialize into a stall, ±20% jitter on spacing.

**Why:**
- The current `RateLimiter` is a fixed 100 ms spacing with zero jitter — steady,
  machine-like cadence that bot detectors flag.
- Jitter keeps the same *average* rate (no throughput loss) while breaking the
  clockwork pattern.
- Burst capacity keeps the parallel-fetch phase of `pull_nse_data` fast.
- The old `RateLimitedSession`/`get_rate_limiter` are left untouched for the
  web-screener/news tools (out of scope).

---

## D-07 — Retry semantics: 401/403 = re-prime, 429 = backoff (don't clear cookies)

**Status:** Decided
**Date:** 2026-08-11

**Decision:** In `StealthSession`:
- Transient failures / 429 → jittered exponential backoff, retry (default 3).
- 401/403 (cookie/fingerprint rejection) → clear that source's cookies,
  re-prime once with backoff, retry once; if still failing raise `CookieError`.
- Do NOT clear cookies on 429 — that's a rate-limit signal, not cookie expiry,
  and clearing forces a wasteful re-prime that can compound the throttle.

**Why:** current code treats 401/403/429 identically (clear + re-prime).
Distinguishing the two cases is both more correct and less likely to trip the
WAF harder.

---

## D-08 — Validate responses, not just status codes

**Status:** Decided
**Date:** 2026-08-11

**Decision:** `StealthSession` applies per-source response validation:
- Downloads: if `Content-Type` contains `text/html` ⇒ treat as blocked /
  not-ready (raise/None), never save an HTML block page as XBRL.
- JSON: if the body is empty or an error envelope (e.g. `{"message": ...}`) ⇒
  treat as failure so retry logic fires on the real failures.

**Why:** NSE returns HTTP 200 with an HTML block page or a JSON error body when
a cookie is stale or data isn't ready. The current code returns `response.content`
on any 200 and `_safe_json` returns `{}` on decode failure, silently storing
garbage.

---

## D-09 — Modular `SourceConfig`/adapter architecture (NSE now, BSE/SEC later)

**Status:** Decided
**Date:** 2026-08-11

**Decision:** A new `src/scrapers/` package separates transport (source-agnostic)
from source config:
- `config.py`: `SourceConfig` dataclass + registry keyed by `(country, source)`.
- `fingerprint.py`, `throttle.py`, `cookies.py`, `session.py`: reusable transport.
- `sources/nse.py`: NSE adapter (warm-up, endpoints, referer base, validators).
- `sources/bse.py`, `sources/sec.py`: skeleton configs ready for future work.
- `src/tools/nse/client.py` becomes a thin NSE facade preserving
  `NSEApiClient`, `NSEIndia`, `ENDPOINTS`, `CookieError`, `_call`, `_safe_json`,
  `fetch_xbrl_content`, `fetch_url_content`, the `*_xbrls()` wrappers, and
  `process_xbrl` exactly.

**Why:** Voyager will scrape US SEC company filings and possibly BSE next. The
transport (TLS impersonation, priming, throttling, cookie store, retries) is
identical in principle across exchanges; only config differs (base URL, warm-up,
headers, endpoints, validation, date format). The XBRL parser already reads any
XBRLi instance (including `in-bse-fin` namespaces), so parsing stays shared.

**Hard boundary:** `api.py`, `cli.py`, `mcp_server.py`, `client/`, `src/db/*`,
`src/models/*`, `src/jobs.py`, `src/services/*` signatures, DB write semantics,
response schemas, and env semantics (`NSE_CALLS_PER_SECOND`,
`NSE_MAX_XBRL_CONCURRENCY`, `MAX_CONCURRENT_PULLS`) are unchanged.

---

## D-11 — Optional proxy wiring (config-only, env-driven)

**Status:** Decided
**Date:** 2026-08-11

**Decision:** `SourceConfig.proxy` (env `NSE_PROXY`) is threaded into
`StealthSession.request(..., proxies=...)`. No proxy rotation strategy is
implemented (that belongs to deployment/infra); this just makes a proxy
first-class at the transport level so IP-rotation can be added later without a
code change.

**Why:** the user flagged remote/Render deployments as a future target, and
IP reputation is one of the signals NSE checks. Cheap to add now, no behavior
change when unset.

---

## D-10 — Impersonation profile pinned and env-overridable

**Status:** Decided
**Date:** 2026-08-11

**Decision:** Default `impersonate="chrome131"` (recent, maintained profile),
overridable via env `NSE_IMPERSONATE`. Documented as something to bump
periodically as WAFs learn older profiles (stealthkit's pinned `chrome110` was
already dated).

**Why:** a pinned, known-good profile gives reproducible behavior while keeping
an escape hatch to rotate without a code change.

---

## D-12 — `NSEApiClient` stays a thin facade; transport owns all anti-detection

**Status:** Implemented
**Date:** 2026-08-11

**Decision:** `src/tools/nse/client.py` was rewritten to delegate every HTTP
concern to `StealthSession`, keeping the exact public surface consumers rely on:
`NSEApiClient`, `NSEIndia`, `ENDPOINTS` (now imported from the NSE adapter),
`CookieError` (re-exported from `src/scrapers.session`), `_call`, `_safe_json`,
`_set_cookies`, `fetch_xbrl_content`, `fetch_url_content`, the `*_xbrls()`
wrappers, `process_xbrl`, `get_random_symbol`.

**Details:**
- `_call` maps transport outcomes to the old contract: success → response;
  `SessionExhausted`/transient → `None`; `CookieError` → raised (unchanged).
- `_set_cookies(symbol)` now returns `session.prime(force=True)`; `symbol` kept
  only for signature compatibility with tests/callers.
- Referer defaults to the get-quotes page for the target symbol (D-05/D-03).
- `fetch_url_content`/`fetch_xbrl_content` apply a download validator (D-08)
  and return `None` on exhaustion instead of saving a block page.

**Why:** services (`src/services/nse.py`), core, CLI, and MCP all consume the
facade. Keeping it thin means those callers required zero changes.

---

## D-13 — Response validation runs inside the transport as a retryable hook

**Status:** Implemented
**Date:** 2026-08-11

**Decision:** `StealthSession.request(..., validate=...)` applies a per-call
validator; a validator failure (e.g. `BlockedResponse` on a `text/html`
download or API response) is treated like a failed attempt: back off, retry,
and if it never clears, raise `SessionExhausted`. A block page is therefore
never returned as data, and is never saved to disk.

**Why:** the facade must not inspect bodies after the fact — it needs the
transport to retry so stale-cookie/HTML-block responses are handled at the same
layer that manages cookies and backoff.

---

## D-14 — Cookie jar is runtime data: gitignored, Path-coerced

**Status:** Implemented
**Date:** 2026-08-11

**Decision:**
- `FileCookieStore` coerces its path to `pathlib.Path` (the config layer passes
  env strings) so `parent.mkdir`/`write_text` behave consistently.
- `data/` was added to `.gitignore` — the persisted Akamai jar
  (`data/cookies/nse_cookies.json`) is machine/IP-bound and must never be
  committed.

---

## D-15 — Live validation passed; test strategy layered

**Status:** Implemented
**Date:** 2026-08-11

**Decision:** The new transport was validated two ways:
- **Live smoke (real NSE):** fresh prime → `announcements_xbrls("TCS")` = 3342
  records in ~1.1s; `shareholding_xbrls("TCS")` = 20 records in ~1.3s; Akamai
  `_abck`/`AKA_A2`/`nsit` cookies persisted to `data/cookies/nse_cookies.json`
  and reused on the next process (fast re-entry). The existing live test
  `test_nse_financials_fetch` (SKYGOLD, full XBRL download + parse) passes.
- **Unit tests** (`tests/test_scrapers.py`): fingerprint stability, throttle
  burst + jitter, cookie store round-trip + read-only-FS fallback, prime
  once-per-TTL, 429-doesn't-clear-cookies, 401 re-prime→`CookieError`,
  validation-retry→`SessionExhausted`, 500-retry-then-success — all with the
  curl layer mocked.
- `tests/test_nse.py` transport mocks were updated to the facade contract
  (mock `session.request` instead of `RateLimitedSession`).

---

<!-- Further decisions appended as the implementation progresses. -->

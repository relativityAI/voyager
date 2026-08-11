"""Voyager admin panel — single-page Streamlit UI.

Run:  streamlit run admin_panel/app.py
"""

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from prometheus_client.parser import text_string_to_metric_families

from admin_panel import config as config_mod
from admin_panel import db_stats
from admin_panel.client import PanelHTTPError, VoyagerClient
from admin_panel.registry import ALL_ENDPOINTS, AUTH_LABELS

st.set_page_config(page_title="Voyager Admin", page_icon="🛰️", layout="wide")

ASSETS = Path(__file__).resolve().parent.parent / "src" / "assets"
NIFTY_CSV = ASSETS / "nifty_midcap_150.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def init_state() -> None:
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("health_results", None)
    st.session_state.setdefault("pull_submit_results", None)
    st.session_state.setdefault("job_auto_refresh", True)
    st.session_state.setdefault("wake_results", None)


def current_cfg() -> config_mod.PanelConfig:
    return config_mod.PanelConfig(
        api_base_url=st.session_state.get("inp_api_base", config_mod.DEFAULTS["api_base_url"]).rstrip("/"),
        api_key=st.session_state.get("inp_api_key", ""),
        admin_key=st.session_state.get("inp_admin_key", ""),
        mongodb_url=st.session_state.get("inp_mongo_url", ""),
        mongodb_db_name=st.session_state.get("inp_mongo_db", "voyager"),
    )


def build_client() -> VoyagerClient:
    cfg = current_cfg()
    return VoyagerClient(cfg.api_base_url, cfg.api_key, cfg.admin_key)


def log_request(method: str, path: str, status: int, ms: float) -> None:
    st.session_state.history.append(
        {
            "time": time.strftime("%H:%M:%S"),
            "method": method,
            "path": path,
            "status": status,
            "ms": round(ms, 1),
        }
    )


def run_request(
    client: VoyagerClient,
    method: str,
    path: str,
    params=None,
    body=None,
    admin: bool = False,
    timeout: int = 120,
    retries: int = 3,
):
    """Execute a request through the client, log to history, return (resp, err)."""
    try:
        resp = client.request(
            method, path, params=params, json=body, admin=admin,
            timeout=timeout, retries=retries,
        )
        log_request(method, path, resp.status_code, resp.elapsed_ms)
        return resp, None
    except PanelHTTPError as exc:
        log_request(method, path, exc.status_code, 0.0)
        return None, exc


@st.cache_data(ttl=30, show_spinner=False)
def cached_health(base_url: str, api_key: str) -> dict:
    return VoyagerClient(base_url, api_key).health()


def status_pill(state: str) -> None:
    colors = {
        "ok": "🟢 ok",
        "waking": "🟡 waking up (cold start)",
        "down": "🔴 down",
        "auth": "🟠 auth error",
        "no_config": "⚪ not configured",
    }
    st.caption(colors.get(state, f"⚪ {state}"))


def parse_symbols(text: str) -> list:
    syms = set()
    for part in text.replace("\n", " ").replace(",", " ").split():
        s = part.strip().upper().replace(".", "").replace(" ", "")
        if s:
            syms.add(s)
    return sorted(syms)


@st.cache_data(ttl=600, show_spinner=False)
def load_symbol_index(mongo_url: str, db_name: str) -> list:
    """Autocomplete index: Nifty 150 CSV + symbols already in the DB."""
    entries = {}
    if NIFTY_CSV.exists():
        try:
            df = pd.read_csv(NIFTY_CSV)
            for _, row in df.iterrows():
                sym = str(row.get("Symbol", "")).strip().upper()
                if sym:
                    entries[sym] = str(row.get("Company Name", "")).strip()
        except Exception:  # noqa: BLE001
            pass
    if mongo_url:
        try:
            _, db = db_stats.connect(mongo_url, db_name)
            for sym in db_stats.distinct_symbols(db):
                entries.setdefault(sym, "")
        except Exception:  # noqa: BLE001
            pass
    return [f"{s} — {entries[s]}" if entries[s] else s for s in sorted(entries)]


def display_response(resp) -> None:
    if resp is None:
        return
    status_ok = 200 <= resp.status_code < 300
    st.markdown(
        f"**HTTP {resp.status_code}** "
        f"{'✅' if status_ok else '⚠️'} · {resp.elapsed_ms} ms"
    )
    data = resp.json if isinstance(resp.json, (dict, list)) else None
    if data is None:
        st.code(resp.text or "(empty response)", language="text")
        return

    df = to_dataframe(data)
    if df is not None:
        view = st.radio("View", ["Table", "JSON"], horizontal=True, key=f"view_{id(resp)}")
        if view == "Table":
            st.dataframe(df, width='stretch', hide_index=True)
            st.download_button(
                "⬇️ Download CSV",
                df.to_csv(index=False).encode(),
                file_name="response.csv",
                mime="text/csv",
            )
        else:
            st.json(data)
        st.download_button(
            "⬇️ Download JSON",
            json.dumps(data, indent=2).encode(),
            file_name="response.json",
            mime="application/json",
        )
    else:
        st.json(data)
        st.download_button(
            "⬇️ Download JSON",
            json.dumps(data, indent=2).encode(),
            file_name="response.json",
            mime="application/json",
        )


def to_dataframe(data):
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return pd.json_normalize(data)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return pd.json_normalize(v)
    return None


def banner_for_error(err: PanelHTTPError, admin_key_set: bool) -> None:
    if err.status_code in (401, 403):
        if admin_key_set:
            st.error(f"Auth failed (HTTP {err.status_code}): {err.detail}")
        else:
            st.warning(
                f"Auth failed (HTTP {err.status_code}): {err.detail} — "
                "set the API key in the sidebar (and the admin key for /admin routes)."
            )
    elif err.status_code == 422:
        st.error(f"Invalid parameters (HTTP 422): {err.detail}")
    else:
        st.error(f"HTTP {err.status_code}: {err.detail}")


def status_color_style(df: pd.DataFrame) -> pd.DataFrame:
    colors = {
        "queued": "#fef3c7",
        "running": "#dbeafe",
        "done": "#dcfce7",
        "failed": "#fee2e2",
    }
    if "status" not in df.columns:
        return df.style
    return df.style.apply(
        lambda row: [f"background-color: {colors.get(row['status'], '')}" for _ in row],
        axis=1,
    )


def job_rows(client: VoyagerClient, limit: int = 50) -> tuple[list, str | None]:
    try:
        resp = client.get("/pull/jobs", params={"limit": limit}, timeout=30)
    except PanelHTTPError as exc:
        return [], exc.detail
    rows = []
    for j in (resp.json or []):
        start = j.get("started_at")
        fin = j.get("finished_at")
        duration = None
        if start and fin:
            try:
                duration = round(
                    (pd.Timestamp(fin) - pd.Timestamp(start)).total_seconds(), 1
                )
            except Exception:  # noqa: BLE001
                duration = None
        rows.append(
            {
                "symbol": j.get("symbol"),
                "filing_type": j.get("filing_type"),
                "refresh": j.get("refresh", False),
                "status": j.get("status"),
                "created_at": j.get("created_at", "")[:19].replace("T", " "),
                "duration_s": duration,
                "job_id": j.get("job_id"),
                "result": j.get("result"),
                "error": j.get("error"),
            }
        )
    return rows, None


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def render_sidebar():
    with st.sidebar:
        st.title("🛰️ Voyager Admin")
        st.caption("Local admin panel for the Voyager API")

        with st.expander("**Voyager API**", expanded=True):
            st.text_input(
                "API endpoint",
                key="inp_api_base",
                placeholder="https://voyager.onrender.com",
            )
            st.text_input(
                "API key",
                type="password",
                key="inp_api_key",
                placeholder="vgr_…",
                help="X-API-Key for data endpoints.",
            )
            st.text_input(
                "Admin key",
                type="password",
                key="inp_admin_key",
                placeholder="hex…",
                help="X-Voyager-Admin-Key for /admin routes (key management).",
            )

        with st.expander("**MongoDB Atlas**", expanded=True):
            st.text_input(
                "Connection string",
                type="password",
                key="inp_mongo_url",
                placeholder="mongodb+srv://user:pass@cluster.mongodb.net/",
                help="Used read-only for the Database Stats tab.",
            )
            st.text_input("Database name", key="inp_mongo_db")

        c1, c2 = st.columns(2)
        if c1.button("💾 Save config", width='stretch'):
            config_mod.save(current_cfg())
            st.toast("Saved to " + str(config_mod.CONFIG_FILE))
        if c2.button("♻️ Reset defaults", width='stretch'):
            env = config_mod.env_defaults()
            st.session_state["inp_api_base"] = env.api_base_url
            st.session_state["inp_api_key"] = env.api_key
            st.session_state["inp_admin_key"] = env.admin_key
            st.session_state["inp_mongo_url"] = env.mongodb_url
            st.session_state["inp_mongo_db"] = env.mongodb_db_name
            st.rerun()

        st.divider()
        health = cached_health(current_cfg().api_base_url, current_cfg().api_key)
        st.markdown("**API status**")
        status_pill(health["state"])
        st.caption(f"endpoint: `{current_cfg().api_base_url or '(none)'}`")

        if st.session_state.health_results:
            st.caption(
                f"last check: {st.session_state.health_results.get('summary', '')}"
            )


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


def tab_overview(client: VoyagerClient):
    st.subheader("Overview")

    c1, c2, c3 = st.columns([1, 1, 2])
    if c1.button("🏥 Run health checks", width='stretch'):
        results = {}
        for name, path, timeout in (
            ("root", "/", 10),
            ("healthz", "/healthz", 10),
            ("readyz", "/readyz", 15),
        ):
            try:
                r = client.get(path, timeout=timeout, retries=0)
                results[name] = {"ok": r.status_code == 200, "status": r.status_code,
                                 "ms": r.elapsed_ms, "body": r.json}
            except PanelHTTPError as exc:
                results[name] = {"ok": False, "status": exc.status_code,
                                 "ms": 0, "body": exc.detail}
        ok = sum(1 for v in results.values() if v["ok"])
        st.session_state.health_results = {
            "results": results,
            "summary": f"{ok}/3 healthy",
        }
    if c2.button("☀️ Wake up API", width='stretch'):
        with st.status("Waking the API…", expanded=True) as status:
            def progress(msg):
                status.update(label=msg)
            result = client.wake(progress=progress)
            st.session_state.wake_results = result
            status.update(
                label=f"Wake complete ({result.get('waited_seconds', 0)}s)",
                state="complete" if result["ok"] else "error",
            )

    if st.session_state.wake_results:
        r = st.session_state.wake_results
        st.info(f"Wake: {r.get('state')} after {r.get('waited_seconds', 0)}s")
        if not r.get("ok"):
            st.error(r.get("detail", "API did not wake."))

    results = (st.session_state.health_results or {}).get("results", {})
    if results:
        cols = st.columns(3)
        for col, (name, r) in zip(cols, results.items()):
            with col:
                st.metric(
                    f"/{name}",
                    "healthy" if r["ok"] else f"HTTP {r['status']}",
                    delta=f"{r['ms']} ms",
                    delta_color="off",
                )
                if r["body"] and isinstance(r["body"], dict):
                    st.caption(str(r["body"])[:80])
    else:
        st.caption("Run a health check to see status." + (
            " — first hit after a cold start can take up to a minute."
        ))

    st.divider()
    cfg = current_cfg()
    st.markdown("**Configuration**")
    st.json(
        {
            "api_endpoint": cfg.api_base_url,
            "api_key_set": bool(cfg.api_key),
            "admin_key_set": bool(cfg.admin_key),
            "mongodb_url_set": bool(cfg.mongodb_url),
            "mongodb_db": cfg.mongodb_db_name,
            "api_version": client.version(),
        }
    )


def tab_pull_manager(client: VoyagerClient):
    st.subheader("Pull Manager")
    st.caption("Submit async NSE XBRL pulls. Requires an API key with `data:write` scope.")

    symbols = load_symbol_index(
        current_cfg().mongodb_url, current_cfg().mongodb_db_name
    )

    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("**Symbols** (comma / space / newline separated)")
        st.text_area(
            "Symbols",
            key="inp_pull_symbols",
            height=110,
            placeholder="RELIANCE\nTCS, INFY, HDFCBANK",
            label_visibility="collapsed",
        )
    with c2:
        st.markdown("**Quick pick**")
        picked = st.selectbox(
            "Quick pick",
            [""] + symbols,
            key="inp_quick_pick",
            label_visibility="collapsed",
        )
        if picked:
            sym = picked.split(" — ")[0]
            existing = st.session_state.get("inp_pull_symbols", "")
            if sym not in parse_symbols(existing):
                st.session_state["inp_pull_symbols"] = (
                    existing + (", " if existing else "") + sym
                )
                st.rerun()
        upload = st.file_uploader(
            "Upload list (.txt/.csv)", type=["txt", "csv"], key="inp_pull_file"
        )
        if upload is not None:
            text = upload.getvalue().decode("utf-8", errors="replace")
            fresh = parse_symbols(text)
            existing = parse_symbols(st.session_state.get("inp_pull_symbols", ""))
            merged = sorted(set(existing) | set(fresh))
            st.session_state["inp_pull_symbols"] = ", ".join(merged)
            st.rerun()

    c1, c2, c3 = st.columns([1, 1, 2])
    filing_type = c1.selectbox(
        "filing_type", ["quarterly", "annual"], key="inp_pull_ft"
    )
    refresh = c2.checkbox("refresh (re-parse existing)", key="inp_pull_refresh")
    c3.text_input("country / source", value="in / nse", disabled=True)

    if st.button("🚀 Start pulls", type="primary", width='stretch'):
        symbols_to_pull = parse_symbols(st.session_state.get("inp_pull_symbols", ""))
        if not symbols_to_pull:
            st.warning("Enter at least one symbol.")
        else:
            results = []
            with st.status(f"Submitting {len(symbols_to_pull)} pull(s)…") as status:
                for i, sym in enumerate(symbols_to_pull):
                    status.update(label=f"{sym} ({i + 1}/{len(symbols_to_pull)})")
                    try:
                        r = client.post(
                            "/pull",
                            params={
                                "symbol": sym,
                                "country": "in",
                                "source": "nse",
                                "filing_type": filing_type,
                                "refresh": refresh,
                            },
                            timeout=60,
                            ok_status=(202,),
                        )
                        body = r.json or {}
                        results.append(
                            {
                                "symbol": sym,
                                "result": "✅ queued",
                                "job_id": body.get("job_id"),
                                "detail": body.get("status", ""),
                            }
                        )
                    except PanelHTTPError as exc:
                        results.append(
                            {"symbol": sym, "result": "❌ failed", "job_id": None,
                             "detail": exc.detail}
                        )
                status.update(label="Done", state="complete")
            st.session_state.pull_submit_results = results
            st.session_state.job_auto_refresh = True

    if st.session_state.pull_submit_results:
        st.markdown("**Submission results**")
        st.dataframe(
            pd.DataFrame(st.session_state.pull_submit_results),
            width='stretch',
            hide_index=True,
        )

    st.divider()
    st.markdown("**Recent jobs**")
    auto = st.toggle(
        "Auto-refresh every 3s while open",
        key="job_auto_refresh",
        help="Polls GET /pull/jobs. Uses the key's rate budget (~20 req/min).",
    )
    if not auto:
        st.caption("Auto-refresh paused.")
    jobs_table(client)


@st.fragment(run_every=3.0)
def jobs_table(client: VoyagerClient):
    auto = st.session_state.get("job_auto_refresh", True)
    if not auto:
        return
    rows, err = job_rows(client, limit=50)
    if err:
        if "403" in err or "401" in err:
            st.warning(
                f"Could not list jobs ({err}). The API key needs the `data:write` "
                "scope — create one in the API Keys tab."
            )
        else:
            st.error(f"Could not list jobs: {err}")
        return
    if not rows:
        st.caption("No pull jobs yet.")
        return
    df = pd.DataFrame(rows)
    active = int(df["status"].isin(["queued", "running"]).sum())
    st.caption(f"{len(df)} recent jobs · {active} active")

    f1, f2 = st.columns([1, 2])
    sym_filter = f1.text_input("Filter symbol", key="inp_job_sym")
    status_filter = f2.multiselect(
        "Filter status",
        ["queued", "running", "done", "failed"],
        default=[],
        key="inp_job_status",
    )
    view = df
    if sym_filter:
        view = view[view["symbol"].str.contains(sym_filter.upper(), na=False)]
    if status_filter:
        view = view[view["status"].isin(status_filter)]

    display = view.drop(columns=["result", "error"])
    st.dataframe(
        status_color_style(display),
        width='stretch',
        hide_index=True,
        column_config={
            "job_id": st.column_config.TextColumn("job_id", width="medium"),
            "duration_s": st.column_config.NumberColumn("duration_s", format="%.1f"),
        },
    )
    for _, row in view.iterrows():
        if row.get("result") or row.get("error"):
            with st.expander(f"{row['symbol']} · {row['job_id']} · {row['status']}"):
                if row.get("error"):
                    st.error(row["error"])
                if row.get("result"):
                    st.json(row["result"])


def tab_playground(client: VoyagerClient):
    st.subheader("Playground")
    st.caption("Call any Voyager endpoint. Endpoint list mirrors the API surface.")

    mode = st.radio(
        "Mode",
        ["Endpoint picker", "Raw request"],
        horizontal=True,
        key="inp_pg_mode",
    )

    if mode == "Endpoint picker":
        labels = [
            f"{e['group']} · {e['name']}  ({e['method']} {e['path']})"
            for e in ALL_ENDPOINTS
        ]
        chosen = st.selectbox(
            "Endpoint", labels, key="inp_pg_endpoint", label_visibility="collapsed"
        )
        ep = ALL_ENDPOINTS[labels.index(chosen)]
        st.markdown(
            f"**{ep['method']} `{ep['path']}`** "
            f"· auth: `{AUTH_LABELS.get(ep['auth'])}`"
        )
        if ep.get("description"):
            st.caption(ep["description"])

        path = ep["path"]
        params = {}
        for p in ep.get("params", []):
            val = render_param(p)
            if p.get("in_path"):
                path = path.replace("{" + p["name"] + "}", str(val) if val else "…")
            elif val is not None and not (p["type"] == "bool3" and val == "null"):
                params[p["name"]] = val

        body = None
        if ep.get("body") is not None:
            body_text = st.text_area(
                "Request body (JSON)",
                value=json.dumps(ep["body"], indent=2),
                height=180,
                key="inp_pg_body",
            )
            try:
                body = json.loads(body_text)
            except ValueError as exc:
                st.error(f"Invalid JSON body: {exc}")

        admin = ep["auth"] == "admin"
        if st.button("▶️ Execute", type="primary", width='stretch'):
            with st.spinner("Requesting…"):
                resp, err = run_request(
                    client, ep["method"], path, params=params, body=body,
                    admin=admin, timeout=120,
                )
            if err:
                banner_for_error(err, bool(current_cfg().admin_key))
            else:
                display_response(resp)
    else:
        c1, c2 = st.columns([1, 3])
        method = c1.selectbox(
            "Method",
            ["GET", "POST", "DELETE", "PUT", "PATCH"],
            key="inp_pg_method",
        )
        path = c2.text_input("Path", value="/financial-metrics", key="inp_pg_path")
        params_text = st.text_area(
            "Query params (one `key=value` per line)",
            height=90,
            key="inp_pg_raw_params",
        )
        body_text = st.text_area(
            "Body (JSON, optional)", height=90, key="inp_pg_raw_body"
        )
        admin = st.checkbox("Use admin key (X-Voyager-Admin-Key)", key="inp_pg_raw_admin")
        if st.button("▶️ Execute raw", type="primary"):
            params = {}
            for line in params_text.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                params[k.strip()] = v.strip()
            body = None
            if body_text.strip():
                try:
                    body = json.loads(body_text)
                except ValueError as exc:
                    st.error(f"Invalid JSON body: {exc}")
                    body = False
            if body is False:
                pass
            else:
                with st.spinner("Requesting…"):
                    resp, err = run_request(
                        client, method, path, params=params or None, body=body,
                        admin=admin, timeout=120,
                    )
                if err:
                    banner_for_error(err, bool(current_cfg().admin_key))
                else:
                    display_response(resp)

    st.divider()
    st.markdown("**Request history** (this session)")
    hist = pd.DataFrame(st.session_state.history)
    if hist.empty:
        st.caption("No requests yet.")
    else:
        st.dataframe(hist, width='stretch', hide_index=True)
        if st.button("Clear history"):
            st.session_state.history = []
            st.rerun()


def render_param(p: dict):
    ptype = p.get("type", "text")
    label = p.get("label", p["name"])
    key = f"pg_{p['name']}"
    help_text = p.get("help")
    required = p.get("required", False)
    if ptype in ("text", "symbol"):
        return st.text_input(
            label,
            value=str(p.get("default", "")),
            key=key,
            placeholder="required" if required else "",
            help=help_text,
        )
    if ptype == "int":
        return st.number_input(
            label,
            value=int(p.get("default", 0)),
            min_value=int(p.get("min", 0)),
            max_value=int(p.get("max", 10**9)),
            step=1,
            key=key,
            help=help_text,
        )
    if ptype == "bool":
        return st.checkbox(label, value=bool(p.get("default", False)), key=key, help=help_text)
    if ptype == "bool3":
        options = p.get("options", [])
        labels = [o["label"] for o in options]
        chosen = st.selectbox(label, labels, key=key)
        return next(o["value"] for o in options if o["label"] == chosen)
    if ptype == "select":
        options = p.get("options", [])
        default = p.get("default")
        idx = options.index(default) if default in options else 0
        return st.selectbox(label, options, index=idx, key=key, help=help_text)
    return st.text_input(label, key=key, help=help_text)


def tab_db_stats():
    st.subheader("Database Stats")
    cfg = current_cfg()
    if not cfg.mongodb_url:
        st.info(
            "No MongoDB connection string set. Add `MONGODB_URL` (Atlas) in the "
            "sidebar — the panel connects read-only for these stats. Your machine "
            "must be on the Atlas Network Access allowlist."
        )
        return

    @st.cache_resource(show_spinner=False)
    def connect_db(url: str, name: str):
        return db_stats.connect(url, name)

    try:
        _client, db = connect_db(cfg.mongodb_url, cfg.mongodb_db_name)
    except db_stats.DBError as exc:
        st.error(f"Could not connect to Atlas: {exc}")
        st.caption(
            "Check the connection string, that your IP is on the Atlas Network "
            "Access allowlist, and that the database name is right."
        )
        return

    info = db_stats.server_info(db)
    if isinstance(info, dict) and "error" in info:
        st.error(f"Stats failed: {info['error']}")
        return

    st.caption(f"Connected to `{cfg.mongodb_db_name}` on MongoDB {info['server_version']} ({info['engine']})")
    m = st.columns(6)
    m[0].metric("Collections", info["collections"])
    m[1].metric("Documents", f"{info['total_documents']:,}")
    m[2].metric("Data", f"{info['data_size_mb']} MB")
    m[3].metric("Storage", f"{info['storage_size_mb']} MB")
    m[4].metric("Indexes", f"{info['index_size_mb']} MB")
    m[5].metric("DB", info["db_name"])

    st.divider()
    st.markdown("**Collections**")
    rows = db_stats.collection_table(db)
    if isinstance(rows, dict) and "error" in rows:
        st.error(rows["error"])
    else:
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

        st.markdown("**Collection detail**")
        chosen = st.selectbox(
            "Collection",
            [r["collection"] for r in rows],
            key="inp_db_coll",
        )
        detail = db_stats.collection_detail(db, chosen)
        if isinstance(detail, dict) and "error" in detail:
            st.error(detail["error"])
        else:
            d = st.columns(3)
            d[0].metric("Documents", f"{detail['documents']:,}")
            if detail.get("coverage"):
                cov = detail["coverage"]
                d[1].metric("Period range", f"{cov.get('min_period', '?')} → {cov.get('max_period', '?')}")
                d[2].metric("Distinct periods", cov.get("distinct_periods", 0))
            if detail.get("filing_types"):
                st.markdown("**filing_type distribution**")
                st.bar_chart(
                    pd.Series(detail["filing_types"]).rename("docs"),
                    horizontal=True,
                )
            if detail.get("top_symbols"):
                st.markdown("**Top symbols**")
                st.dataframe(
                    pd.DataFrame(detail["top_symbols"]),
                    width='stretch',
                    hide_index=True,
                )
            if detail.get("sample_doc"):
                with st.expander("Sample document"):
                    st.json(detail["sample_doc"])

    st.divider()
    st.markdown("**Pull job analytics**")
    jstats = db_stats.job_stats(db)
    if isinstance(jstats, dict) and "error" in jstats:
        st.error(jstats["error"])
    else:
        jc = st.columns(6)
        for i, status in enumerate(["queued", "running", "done", "failed"]):
            jc[i].metric(status.capitalize(), jstats["by_status"].get(status, 0))
        jc[4].metric("Total", jstats["total"])
        jc[5].metric(
            "Avg duration (s)",
            jstats["avg_duration_sec"] if jstats["avg_duration_sec"] is not None else "—",
        )
        if jstats.get("per_day"):
            jd = pd.DataFrame(jstats["per_day"])
            st.markdown("**Jobs per day**")
            st.bar_chart(jd.set_index("date")["jobs"])
        if jstats.get("recent_failed"):
            st.markdown("**Recent failed jobs**")
            fails = []
            for f in jstats["recent_failed"]:
                fails.append(
                    {
                        "job_id": str(f.get("job_id", ""))[:18],
                        "symbol": f.get("symbol"),
                        "created_at": str(f.get("created_at", ""))[:19],
                        "error": str(f.get("error", ""))[:120],
                    }
                )
            st.dataframe(pd.DataFrame(fails), width='stretch', hide_index=True)

    st.divider()
    st.markdown("**API key analytics**")
    kstats = db_stats.key_stats(db)
    if isinstance(kstats, dict) and "error" in kstats:
        st.error(kstats["error"])
    else:
        kc = st.columns(5)
        kc[0].metric("Total keys", kstats["total"])
        kc[1].metric("Enabled", kstats["enabled"])
        kc[2].metric("Revoked", kstats["revoked"])
        kc[3].metric("Expired", kstats["expired"])
        kc[4].metric("Scopes", ", ".join(f"{k}:{v}" for k, v in kstats["scopes"].items()))


def tab_api_keys(client: VoyagerClient):
    st.subheader("API Keys")
    if not current_cfg().admin_key:
        st.warning("Set the admin key (VOYAGER_ADMIN_KEY) in the sidebar to manage keys.")
        return

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**Create key**")
        name = st.text_input("Name", key="key_name", placeholder="my-app")
        owner = st.text_input("Owner", key="key_owner", placeholder="optional")
        scopes = st.multiselect(
            "Scopes", ["data:read", "data:write", "admin"],
            default=["data:read"], key="key_scopes",
        )
        c11, c12 = st.columns(2)
        rpm = c11.number_input("RPM", min_value=1, max_value=10000, value=60, step=1, key="key_rpm")
        expires = c12.number_input(
            "Expires in days (0 = never)", min_value=0, value=0, step=1, key="key_expires"
        )
        if st.button("➕ Create key", type="primary"):
            body = {
                "name": name,
                "owner": owner,
                "scopes": scopes or ["data:read"],
                "rpm": int(rpm),
            }
            if expires and int(expires) > 0:
                body["expires_in_days"] = int(expires)
            if not name.strip():
                st.warning("Name is required.")
            else:
                resp, err = run_request(
                    client, "POST", "/admin/keys", body=body, admin=True, timeout=30
                )
                if err:
                    banner_for_error(err, True)
                else:
                    raw = resp.json.get("key")
                    st.success("Key created — copy it now, it won't be shown again.")
                    st.code(raw, language="text")
                    st.download_button(
                        "⬇️ Download key",
                        raw.encode(),
                        file_name=f"{name}-key.txt",
                        mime="text/plain",
                    )

    with c2:
        st.markdown("**List & manage**")
        if st.button("🔄 Refresh list", key="key_refresh"):
            st.rerun()
        resp, err = run_request(client, "GET", "/admin/keys", admin=True, timeout=30)
        if err:
            banner_for_error(err, True)
        else:
            keys = resp.json or []
            if not keys:
                st.caption("No keys yet.")
            else:
                df = pd.DataFrame(keys)
                cols = [c for c in
                        ["name", "prefix", "scopes", "rpm", "enabled", "expires_at",
                         "last_used_at", "created_at", "owner", "revoked_at"] if c in df.columns]
                st.dataframe(df[cols], width='stretch', hide_index=True)

                st.markdown("**Actions**")
                a1, a2 = st.columns(2)
                prefix = a1.selectbox(
                    "Key (prefix)", [k["prefix"] for k in keys], key="key_act_prefix"
                )
                action = a2.selectbox("Action", ["revoke", "enable"], key="key_act_type")
                if st.button("Apply"):
                    path = f"/admin/keys/{prefix}"
                    if action == "revoke":
                        r, e = run_request(client, "DELETE", path, admin=True, timeout=30)
                    else:
                        r, e = run_request(client, "POST", f"{path}/enable", admin=True, timeout=30)
                    if e:
                        banner_for_error(e, True)
                    else:
                        st.success(f"{action} {prefix}: {r.json.get('status')}")
                        st.rerun()


def tab_metrics(client: VoyagerClient):
    st.subheader("Metrics")
    st.caption("Parsed from `GET /metrics` (Prometheus).")

    resp, err = run_request(client, "GET", "/metrics", timeout=30)
    if err:
        banner_for_error(err, bool(current_cfg().admin_key))
        st.caption(
            "If /metrics is disabled, set METRICS_ENABLED=true on the server."
        )
        return

    families = {}
    for family in text_string_to_metric_families(resp.text):
        families[family.name] = list(family.samples)

    counter = families.get("http_requests_total", [])
    histogram = families.get("http_request_duration_seconds", [])

    if not counter and not histogram:
        st.info("No HTTP metrics found yet. Hit a few endpoints first.")
        return

    if counter:
        by_route = {}
        for s in counter:
            route = s.labels.get("route", "unmatched")
            key = (s.labels.get("method", ""), route)
            by_route[key] = by_route.get(key, 0) + s.value
        total = sum(by_route.values())
        err_rate = 0.0
        route_rows = []
        for (method, route), count in sorted(
            by_route.items(), key=lambda kv: kv[1], reverse=True
        ):
            errs = sum(
                s.value for s in counter
                if (s.labels.get("method"), s.labels.get("route")) == (method, route)
                and int(s.labels.get("status", 0)) >= 500
            )
            rate = errs / count * 100 if count else 0
            err_rate += rate
            route_rows.append(
                {"method": method, "route": route, "requests": int(count),
                 "error_rate_%": round(rate, 2)}
            )
        m = st.columns(3)
        m[0].metric("Total requests", f"{int(total):,}")
        m[1].metric("Active routes", len(route_rows))
        m[2].metric("5xx share", f"{round(err_rate, 2)}%")
        rdf = pd.DataFrame(route_rows)
        st.markdown("**Requests by route**")
        st.bar_chart(rdf.set_index("route")["requests"])
        st.markdown("**5xx error rate by route**")
        st.dataframe(rdf, width='stretch', hide_index=True)

    if histogram:
        buckets = {}
        for s in histogram:
            if not s.name.endswith("_bucket"):
                continue
            le = s.labels.get("le", "")
            key = (s.labels.get("method", ""), s.labels.get("route", "unmatched"))
            buckets.setdefault(key, []).append((le, s.value))
        rows = []
        for (method, route), bucket_list in buckets.items():
            bucket_list.sort(key=lambda x: float(x[0]) if x[0] != "+Inf" else 1e18)
            total = bucket_list[-1][1] if bucket_list else 0
            if not total:
                continue
            p50 = _percentile(bucket_list, 0.50)
            p95 = _percentile(bucket_list, 0.95)
            rows.append(
                {"method": method, "route": route, "requests": int(total),
                 "p50_ms": p50, "p95_ms": p95}
            )
        if rows:
            st.markdown("**Latency (ms)**")
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)


def _percentile(bucket_list, p: float):
    total = bucket_list[-1][1]
    target = total * p
    cumulative = 0.0
    for le, count in bucket_list:
        cumulative += count
        if cumulative >= target:
            if le == "+Inf":
                return None
            return round(float(le) * 1000, 1)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    init_state()
    defaults = config_mod.load()
    if "inp_api_base" not in st.session_state:
        st.session_state["inp_api_base"] = defaults.api_base_url
        st.session_state["inp_api_key"] = defaults.api_key
        st.session_state["inp_admin_key"] = defaults.admin_key
        st.session_state["inp_mongo_url"] = defaults.mongodb_url
        st.session_state["inp_mongo_db"] = defaults.mongodb_db_name

    render_sidebar()

    client = build_client()

    tabs = st.tabs(
        ["Overview", "Pull Manager", "Playground", "Database Stats", "API Keys", "Metrics"]
    )
    with tabs[0]:
        tab_overview(client)
    with tabs[1]:
        tab_pull_manager(client)
    with tabs[2]:
        tab_playground(client)
    with tabs[3]:
        tab_db_stats()
    with tabs[4]:
        tab_api_keys(client)
    with tabs[5]:
        tab_metrics(client)


main()

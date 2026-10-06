"""Pull Manager: submit async XBRL pulls and watch the job queue.

Polling is opt-in and backs off. GET /pull/jobs needs a data:write key and
each poll spends that key's per-minute budget, so a fixed 3s timer starves
every other tab of the same key.
"""

import time
from pathlib import Path

import pandas as pd
import streamlit as st

from admin_panel import db_stats
from admin_panel.client import PanelHTTPError
from admin_panel.state import build_client, current_cfg, sidebar

ACTIVE = ("queued", "running")
POLL_FAST = 3.0
POLL_IDLE = 10.0

NIFTY_CSV = Path(db_stats.__file__).resolve().parent.parent / "src" / "assets" / "nifty_midcap_150.csv"


def parse_symbols(text: str) -> list[str]:
    syms = set()
    for part in text.replace("\n", " ").replace(",", " ").split():
        s = part.strip().upper().replace(".", "").replace(" ", "")
        if s:
            syms.add(s)
    return sorted(syms)


@st.cache_data(ttl=600, show_spinner=False)
def load_symbol_index(database_url: str) -> list:
    """Autocomplete index: Nifty 150 CSV + symbols already in the DB."""
    entries = {}
    if NIFTY_CSV.exists():
        try:
            for _, row in pd.read_csv(NIFTY_CSV).iterrows():
                sym = str(row.get("Symbol", "")).strip().upper()
                if sym:
                    entries[sym] = str(row.get("Company Name", "")).strip()
        except Exception:  # noqa: BLE001
            pass
    if database_url:
        try:
            for sym in db_stats.distinct_symbols(db_stats.connect(database_url)):
                if isinstance(sym, str):
                    entries.setdefault(sym, "")
        except Exception:  # noqa: BLE001
            pass
    return [f"{s} — {entries[s]}" if entries[s] else s for s in sorted(entries)]


def _queue_picked_symbol() -> None:
    """Append the quick-pick choice to the symbols field. Idempotent.

    Runs before the form renders, so writing the key here is the supported
    way to seed a form widget rather than mutating it from a callback.
    """
    picked = st.session_state.get("inp_quick_pick", "")
    if not picked:
        return
    sym = picked.split(" — ")[0]
    existing = st.session_state.get("inp_pull_symbols", "")
    if sym not in parse_symbols(existing):
        st.session_state["inp_pull_symbols"] = existing + (", " if existing else "") + sym


def job_rows(client, limit: int = 50) -> tuple[list, PanelHTTPError | None]:
    try:
        resp = client.get("/pull/jobs", params={"limit": limit}, timeout=30)
    except PanelHTTPError as exc:
        return [], exc
    rows = []
    for j in resp.json or []:
        start, fin = j.get("started_at"), j.get("finished_at")
        duration = None
        if start and fin:
            try:
                duration = round((pd.Timestamp(fin) - pd.Timestamp(start)).total_seconds(), 1)
            except Exception:  # noqa: BLE001
                duration = None
        rows.append(
            {
                "job_id": j.get("job_id"),
                "symbol": j.get("symbol"),
                # Task jobs (documents.parse) have no
                # symbol — they run against task_args, not an exchange.
                "task": j.get("task"),
                "source": j.get("source", "nse"),
                "filing_type": j.get("filing_type"),
                "refresh": j.get("refresh", False),
                "status": j.get("status"),
                "created_at": (j.get("created_at") or "")[:19].replace("T", " "),
                "duration_s": duration,
                "result": j.get("result"),
                "error": j.get("error"),
            }
        )
    return rows, None


def status_pill(status: str) -> str:
    return {
        "queued": "🟡 queued",
        "running": "🔵 running",
        "done": "🟢 done",
        "failed": "🔴 failed",
    }.get(status, status)


def _submit_pulls(client, symbols, source, filing_type, refresh) -> list[dict]:
    results = []
    for sym in symbols:
        try:
            r = client.post(
                "/pull",
                params={
                    "symbol": sym,
                    "source": source,
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
                    "ok": True,
                    "status": 202,
                    "job_id": body.get("job_id"),
                    "detail": body.get("status", ""),
                }
            )
        except PanelHTTPError as exc:
            results.append(
                {"symbol": sym, "ok": False, "status": exc.status_code,
                 "job_id": None, "detail": exc.detail}
            )
    return results


def pulls() -> None:
    sidebar()
    cfg = current_cfg()
    client = build_client()
    st.subheader("Pull Manager")
    st.caption("Submit async XBRL pulls (NSE or SEC/EDGAR). Needs a `data:write` API key.")

    symbols = load_symbol_index(cfg.database_url)
    st.selectbox("Quick pick", [""] + symbols, key="inp_quick_pick", label_visibility="collapsed")
    _queue_picked_symbol()

    with st.form("pull_form", border=False):
        st.markdown("**Symbols** (comma / space / newline separated)")
        st.text_area(
            "Symbols",
            key="inp_pull_symbols",
            height=110,
            placeholder="RELIANCE\nTCS, INFY, HDFCBANK",
            label_visibility="collapsed",
        )
        c1, c2, c3 = st.columns(3)
        filing_type = c1.selectbox("filing_type", ["quarterly", "annual"], key="inp_pull_ft")
        source = c2.selectbox("source", ["nse", "sec"], key="inp_pull_source")
        refresh = c3.checkbox(
            "refresh (re-downloads from the exchange, costs a source request)",
            key="inp_pull_refresh",
        )
        start = st.form_submit_button("🚀 Start pulls", type="primary", width="stretch")

    if start:
        wanted = parse_symbols(st.session_state.get("inp_pull_symbols", ""))
        if not wanted:
            st.warning("Enter at least one symbol.")
        else:
            with st.status(f"Submitting {len(wanted)} pull(s)…", expanded=True) as status:
                for i, sym in enumerate(wanted):
                    status.update(label=f"{sym} ({i + 1}/{len(wanted)})")
                results = _submit_pulls(client, wanted, source, filing_type, refresh)
                status.update(label="Done", state="complete")
            st.session_state.pull_result = {"submitted": wanted, "rows": results}
            st.session_state.jobs_active = True

    _render_submissions(client)
    st.divider()
    st.markdown("**Recent jobs**")
    st.toggle(
        "Auto-refresh (spends this key's request budget — 3s while jobs run, 10s when idle)",
        key="job_auto_refresh",
        help="Polls GET /pull/jobs. Leave off unless you're watching a queue drain.",
    )
    jobs_table(client)


def _render_submissions(client) -> None:
    state = st.session_state.get("pull_result")
    if not state:
        return
    rows = state["rows"]
    ok = [r for r in rows if r["ok"]]
    failed = [r for r in rows if not r["ok"]]
    left, right = st.columns([5, 1])
    left.caption(f"Last submit · {len(ok)}/{len(rows)} queued")
    if right.button("🗑 Clear", key="pull_clear", width="stretch"):
        st.session_state.pull_result = None
        st.rerun()

    for r in failed:
        if r["status"] == 503:
            st.warning(f"{r['symbol']}: {r['detail']} — the job queue is full; retry shortly.")
        elif r["status"] == 429:
            st.warning(f"{r['symbol']}: {r['detail']} — wait a minute and retry.")
        else:
            st.error(f"{r['symbol']} (HTTP {r['status']}): {r['detail']}")
    if ok:
        st.success(f"Queued {len(ok)} pull(s).")
    if failed:
        if st.button(f"↻ Retry {len(failed)} rejected", key="pull_retry"):
            with st.status("Retrying…", expanded=True) as status:
                retried = _submit_pulls(
                    client,
                    [r["symbol"] for r in failed],
                    st.session_state.get("inp_pull_source", "nse"),
                    st.session_state.get("inp_pull_ft", "quarterly"),
                    st.session_state.get("inp_pull_refresh", False),
                )
                status.update(label="Done", state="complete")
            st.session_state.pull_result = {"submitted": state["submitted"], "rows": retried}
            st.rerun()


@st.fragment(run_every=POLL_FAST)
def jobs_table(client) -> None:
    st.session_state.setdefault("jobs_rows", [])
    st.session_state.setdefault("jobs_err", None)
    st.session_state.setdefault("jobs_fetched_at", 0.0)
    st.session_state.setdefault("jobs_active", False)

    if not st.session_state.get("job_auto_refresh", False):
        st.caption("Auto-refresh is off.")
    else:
        now = time.monotonic()
        gap = POLL_FAST if st.session_state.jobs_active else POLL_IDLE
        if now - st.session_state.jobs_fetched_at >= gap:
            rows, err = job_rows(client, limit=50)
            st.session_state.jobs_rows = rows
            st.session_state.jobs_err = err
            st.session_state.jobs_fetched_at = now
            st.session_state.jobs_active = any(r["status"] in ACTIVE for r in rows)

    err = st.session_state.jobs_err
    if err is not None:
        # Match on status_code: the detail string for a 403 says
        # "This key requires the 'data:write' scope" and never contains "403".
        if err.status_code in (401, 403):
            st.warning(
                f"Could not list jobs (HTTP {err.status_code}): {err.detail} — the API key "
                "needs the `data:write` scope. Create one in the API Keys tab."
            )
        elif err.status_code == 429:
            st.warning(f"Rate limited (HTTP 429): {err.detail} — turn auto-refresh off.")
        else:
            st.error(f"Could not list jobs (HTTP {err.status_code}): {err.detail}")
        return

    rows = st.session_state.jobs_rows
    if not rows:
        st.caption("No pull jobs yet.")
        return
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)} recent jobs · {int(df['status'].isin(ACTIVE).sum())} active")

    f1, f2 = st.columns([1, 2])
    sym_filter = f1.text_input("Filter symbol", key="inp_job_sym")
    status_filter = f2.multiselect(
        "Filter status", sorted(df["status"].unique()), key="inp_job_status"
    )
    view = df
    if sym_filter:
        view = view[view["symbol"].str.contains(sym_filter.upper(), na=False)]
    if status_filter:
        view = view[view["status"].isin(status_filter)]

    display = view.drop(columns=["result", "error"]).copy()
    display["status"] = display["status"].map(status_pill)
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "job_id": st.column_config.TextColumn("job_id", width="medium"),
            "duration_s": st.column_config.NumberColumn("duration_s", format="%.1f"),
        },
    )
    for _, row in view.iterrows():
        label = f"{row['task'] or row['symbol']} · {row['job_id']}"
        with st.popover(label):
            st.write({k: row[k] for k in ("source", "filing_type", "refresh", "created_at")})
            if row.get("error"):
                st.error(row["error"])
            elif row.get("result"):
                st.json(row["result"])
            else:
                st.caption("No result recorded.")

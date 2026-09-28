"""Database Stats: read-only aggregate queries straight against PostgreSQL.

The whole tab is one cached snapshot. Running it on every render meant a
COUNT(*) across every table on every keystroke anywhere in the app.
"""

import pandas as pd
import streamlit as st

from admin_panel import db_stats
from admin_panel.state import current_cfg, sidebar


def database() -> None:
    sidebar()
    st.subheader("Database Stats")
    cfg = current_cfg()
    if not cfg.database_url:
        st.info(
            "No DATABASE_URL set. Add `DATABASE_URL` in the sidebar — the panel "
            "connects read-only for these stats."
        )
        return

    try:
        engine = _engine(cfg.database_url)
    except db_stats.DBError as exc:
        st.error(str(exc))
        return

    refresh = st.button("🔄 Refresh")
    if st.session_state.get("dbstats") is None or refresh:
        with st.spinner("Querying PostgreSQL…"):
            st.session_state.dbstats = db_stats.snapshot(engine)

    snap = st.session_state.dbstats or {}
    if "error" in snap:
        st.error(snap["error"])
        return

    _server(snap["server"])
    st.divider()
    _collections(engine, snap)
    st.divider()
    _coverage(snap["coverage"])
    st.divider()
    _jobs(snap["jobs"])
    st.divider()
    _keys(snap["keys"])


@st.cache_resource(show_spinner=False)
def _engine(url: str):
    return db_stats.connect(url)


def _notes(items) -> None:
    """Per-table query failures, which used to be swallowed into a silent zero."""
    for note in items or []:
        st.caption(f"⚠️ {note}")


def _server(info) -> None:
    st.caption(f"Connected to PostgreSQL {info['server_version']}")
    m = st.columns(3)
    m[0].metric("Tables", info["tables"])
    m[1].metric("Rows", f"{info['total_rows']:,}")
    m[2].metric("Database", info["db_name"])
    _notes(info.get("errors"))


def _collections(engine, snap) -> None:
    st.markdown("**Collections**")
    rows = snap["collections"]
    _notes([r["error"] for r in rows if r.get("error")])
    tables = [r for r in rows if not r.get("error")]
    if not tables:
        return
    st.dataframe(pd.DataFrame(tables), width="stretch", hide_index=True)

    st.markdown("**Collection detail**")
    chosen = st.selectbox("Collection", [r["collection"] for r in tables], key="inp_db_coll")
    # Query the selected table only, once, then cache: a snapshot across all
    # 13 tables would be ~65 round trips.
    if (st.session_state.get("db_detail") or {}).get("name") != chosen:
        st.session_state.db_detail = {"name": chosen, "data": db_stats.collection_detail(engine, chosen)}
    detail = st.session_state.db_detail["data"]
    if "error" in detail:
        st.error(detail["error"])
        return

    d = st.columns(3)
    d[0].metric("Documents", f"{detail['documents']:,}")
    if detail.get("coverage"):
        cov = detail["coverage"]
        d[1].metric("Period range", f"{cov.get('min_period', '?')} → {cov.get('max_period', '?')}")
        d[2].metric("Distinct periods", cov.get("distinct_periods", 0))
    if detail.get("filing_types"):
        st.markdown("**filing_type distribution**")
        st.bar_chart(pd.Series(detail["filing_types"]).rename("docs"), horizontal=True)
    if detail.get("top_symbols"):
        st.markdown("**Top symbols**")
        st.dataframe(pd.DataFrame(detail["top_symbols"]), width="stretch", hide_index=True)
    _notes(detail.get("notes"))
    if detail.get("sample_doc"):
        with st.expander("Sample document"):
            st.json(detail["sample_doc"])


def _coverage(rows) -> None:
    st.markdown("**Financial-metrics field coverage**")
    st.caption(
        "Fields feeding /financial-metrics (TTM flows + liquidity/turnover ratios). "
        "0% on the newer fields means a re-pull with refresh=true is needed to backfill."
    )
    if not rows:
        return
    df = pd.DataFrame(rows)
    failed = df["error"].notna() if "error" in df.columns else pd.Series([False] * len(df))
    ok = df[~failed].drop(columns=[c for c in ("error",) if c in df.columns])
    if not ok.empty:
        st.dataframe(
            ok,
            width="stretch",
            hide_index=True,
            column_config={
                "coverage_pct": st.column_config.ProgressColumn(
                    "coverage_pct", min_value=0, max_value=100, format="%.1f%%"
                )
            },
        )
    for _, e in df[failed].iterrows():
        st.caption(f"{e['table']}.{e['field']}: {e['error']}")


def _jobs(stats) -> None:
    st.markdown("**Pull job analytics**")
    jc = st.columns(6)
    for i, status in enumerate(("queued", "running", "done", "failed")):
        jc[i].metric(status.capitalize(), stats["by_status"].get(status, 0))
    jc[4].metric("Total", stats["total"])
    avg = stats["avg_duration_sec"]
    jc[5].metric("Avg duration (s)", avg if avg is not None else "—")
    if stats.get("per_day"):
        st.markdown("**Jobs per day**")
        st.bar_chart(pd.DataFrame(stats["per_day"]).set_index("date")["jobs"])
    if stats.get("recent_failed"):
        st.markdown("**Recent failed jobs**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "job_id": f.get("job_id"),
                        "symbol": f.get("symbol") or f.get("task"),
                        "source": f.get("source"),
                        "created_at": str(f.get("created_at", ""))[:19].replace("T", " "),
                        "error": str(f.get("error", ""))[:200],
                    }
                    for f in stats["recent_failed"]
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    _notes(stats.get("errors"))


def _keys(stats) -> None:
    st.markdown("**API key analytics**")
    kc = st.columns(4)
    for i, label in enumerate(("total", "enabled", "revoked", "expired")):
        kc[i].metric(label.capitalize(), stats[label])
    st.caption("Scopes: " + (", ".join(f"{k}: {v}" for k, v in stats["scopes"].items()) or "none"))

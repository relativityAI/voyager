"""Overview: endpoint reachability and what's currently configured."""

import streamlit as st

from admin_panel.client import VoyagerClient
from admin_panel.state import build_client, current_cfg, sidebar

PROBES = (("root", "/", 10), ("healthz", "/healthz", 10), ("readyz", "/readyz", 15))


def overview() -> None:
    sidebar()
    client = build_client()
    st.subheader("Overview")

    c1, c2 = st.columns(2)
    if c1.button("🏥 Run health checks", type="primary", width="stretch"):
        _probe(client)
    if c2.button("☀️ Wake up API", width="stretch"):
        with st.status("Waking the API…", expanded=True) as status:
            result = client.wake(progress=lambda m: status.update(label=m))
        st.session_state.wake = result
        st.rerun()

    _render_wake()
    _render_health()

    st.divider()
    st.markdown("**Configuration**")
    cfg = current_cfg()
    health = st.session_state.get("health") or {}
    st.json(
        {
            "api_endpoint": cfg.api_base_url,
            "api_key_set": bool(cfg.api_key),
            "admin_key_set": bool(cfg.admin_key),
            "database_url_set": bool(cfg.database_url),
            # Fetched during the health check rather than on every render,
            # which used to be an uncached GET /openapi.json per keystroke.
            "api_version": health.get("version", "— run health checks"),
        }
    )


def _probe(client: VoyagerClient) -> None:
    results = {}
    for name, path, timeout in PROBES:
        try:
            r = client.get(path, timeout=timeout, retries=0)
            results[name] = {"ok": r.status_code == 200, "status": r.status_code,
                             "ms": r.elapsed_ms, "body": r.json}
        except Exception as exc:  # noqa: BLE001
            results[name] = {"ok": False, "status": 0, "ms": 0, "body": str(exc)}
    ok = sum(1 for v in results.values() if v["ok"])
    st.session_state.health = {
        "results": results,
        "summary": f"{ok}/{len(PROBES)} healthy",
        "version": client.version(),
    }
    st.rerun()


def _render_health() -> None:
    health = st.session_state.get("health")
    if not health:
        st.caption(
            "Run a health check to see status."
            " — the first hit after a cold start can take up to a minute."
        )
        return
    cols = st.columns(len(PROBES))
    for col, (name, r) in zip(cols, health["results"].items()):
        with col:
            st.metric(
                f"/{name}",
                "healthy" if r["ok"] else f"HTTP {r['status']}",
                delta=f"{r['ms']} ms",
                delta_color="off",
            )
            if isinstance(r["body"], dict):
                st.caption(str(r["body"])[:80])
    st.caption(health["summary"])


def _render_wake() -> None:
    result = st.session_state.get("wake")
    if not result:
        return
    st.info(f"Wake: {result.get('state')} after {result.get('waited_seconds', 0)}s")
    if not result.get("ok"):
        st.error(result.get("detail", "API did not wake."))

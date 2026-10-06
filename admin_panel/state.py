"""Shared session state, config plumbing and render helpers.

Streamlit re-runs the active page on every widget interaction, so a result
produced by a button has to be parked in session_state and rendered from
there. Reading a response straight out of the button branch looks like it
works, but the next keystroke anywhere in the app erases it — which is how a
just-created API key used to disappear before it could be copied.
"""

import json
import time

import pandas as pd
import streamlit as st

from admin_panel import config as config_mod
from admin_panel.client import PanelHTTPError, VoyagerClient

OK_STATUS = (200, 201, 202)


def init_state() -> None:
    defaults = config_mod.load()
    for field, key in (
        ("api_base_url", "inp_api_base"),
        ("api_key", "inp_api_key"),
        ("admin_key", "inp_admin_key"),
        ("database_url", "inp_database_url"),
    ):
        if key not in st.session_state:
            st.session_state[key] = getattr(defaults, field)
    st.session_state.setdefault("history", [])
    for slot in ("health", "wake", "pg_result", "pull_result", "new_key", "dbstats"):
        st.session_state.setdefault(slot, None)


def current_cfg() -> config_mod.PanelConfig:
    return config_mod.PanelConfig(
        api_base_url=st.session_state.get(
            "inp_api_base", config_mod.DEFAULTS["api_base_url"]
        ).rstrip("/"),
        api_key=st.session_state.get("inp_api_key", ""),
        admin_key=st.session_state.get("inp_admin_key", ""),
        database_url=st.session_state.get("inp_database_url", ""),
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


def submit(
    slot: str,
    method: str,
    path: str,
    *,
    client: VoyagerClient,
    params=None,
    body=None,
    admin: bool = False,
    timeout: int = 120,
    ok_status: tuple = OK_STATUS,
) -> dict:
    """Make one request and park the outcome in session_state[slot].

    The caller renders from `render_result`; it never touches the response
    directly, so the result survives later interactions.
    """
    result = {"resp": None, "err": None, "at": time.strftime("%H:%M:%S")}
    try:
        result["resp"] = client.request(
            method, path, params=params, json=body, admin=admin,
            timeout=timeout, ok_status=ok_status,
        )
        log_request(method, path, result["resp"].status_code, result["resp"].elapsed_ms)
    except PanelHTTPError as exc:
        result["err"] = exc
        log_request(method, path, exc.status_code, 0.0)
    st.session_state[slot] = result
    return result


def render_result(slot: str, name: str = "response", clear_label: str = "🗑 Clear") -> None:
    """Render whatever `submit` last stored in `slot`, with a clear control."""
    result = st.session_state.get(slot)
    if not result:
        return
    left, right = st.columns([5, 1])
    left.caption(f"Last result · {result['at']}")
    if right.button(clear_label, key=f"{slot}_clear", width="stretch"):
        st.session_state[slot] = None
        st.rerun()
    if result["err"] is not None:
        banner_for_error(result["err"], bool(current_cfg().admin_key))
    else:
        display_response(result["resp"], name)


def display_response(resp, name: str = "response") -> None:
    if resp is None:
        return
    ok = 200 <= resp.status_code < 300
    st.markdown(f"**HTTP {resp.status_code}** {'✅' if ok else '⚠️'} · {resp.elapsed_ms} ms")
    data = resp.json if isinstance(resp.json, (dict, list)) else None
    if data is None:
        st.code(resp.text or "(empty response)", language="text")
        return

    df = to_dataframe(data)
    if df is None:
        st.json(data)
    else:
        view = st.radio("View", ["Table", "JSON"], horizontal=True, key=f"view_{name}")
        if view == "Table":
            st.dataframe(df, width="stretch", hide_index=True)
            st.download_button(
                "⬇️ Download CSV",
                df.to_csv(index=False).encode(),
                file_name=f"{name}.csv",
                mime="text/csv",
                key=f"csv_{name}",
            )
        else:
            st.json(data)
    st.download_button(
        "⬇️ Download JSON",
        json.dumps(data, indent=2).encode(),
        file_name=f"{name}.json",
        mime="application/json",
        key=f"json_{name}",
    )


def to_dataframe(data):
    """First list-of-objects in the payload, as a table. None if there isn't one."""
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return pd.json_normalize(data)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return pd.json_normalize(v)
    return None


def banner_for_error(err: PanelHTTPError, admin_key_set: bool) -> None:
    status, detail = err.status_code, err.detail
    if status in (401, 403):
        if admin_key_set:
            st.error(f"Auth failed (HTTP {status}): {detail}")
        else:
            st.warning(
                f"Auth failed (HTTP {status}): {detail} — set the API key in the "
                "sidebar (and the admin key for /admin routes)."
            )
    elif status == 422:
        st.error(f"Invalid parameters (HTTP 422): {detail}")
    elif status == 429:
        st.warning(f"Rate limited (HTTP 429): {detail} — turn off job auto-refresh or wait a minute.")
    elif status == 409:
        st.warning(f"Conflict (HTTP 409): {detail}")
    elif status == 503:
        st.warning(f"HTTP 503: {detail} — the job queue is full; retry shortly.")
    else:
        st.error(f"HTTP {status}: {detail}")


@st.cache_data(ttl=30, show_spinner=False)
def cached_health(base_url: str) -> dict:
    # /healthz is public, so the key is deliberately not part of the cache key.
    return VoyagerClient(base_url).health()


def sidebar() -> None:
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

        with st.expander("**PostgreSQL**", expanded=True):
            st.text_input(
                "DATABASE_URL",
                type="password",
                key="inp_database_url",
                placeholder="postgresql+asyncpg://user:pass@host:5432/voyager",
                help="Used read-only for the Database Stats tab.",
            )

        c1, c2 = st.columns(2)
        if c1.button("💾 Save config", width="stretch"):
            config_mod.save(current_cfg())
            st.toast("Saved to " + str(config_mod.CONFIG_FILE))
        if c2.button("♻️ Reset defaults", width="stretch"):
            env = config_mod.env_defaults()
            st.session_state["inp_api_base"] = env.api_base_url
            st.session_state["inp_api_key"] = env.api_key
            st.session_state["inp_admin_key"] = env.admin_key
            st.session_state["inp_database_url"] = env.database_url
            st.rerun()

        st.divider()
        cfg = current_cfg()
        st.markdown("**API status**")
        st.caption(cached_health(cfg.api_base_url)["state"])
        st.caption(f"endpoint: `{cfg.api_base_url or '(none)'}`")
        if st.session_state.get("health"):
            st.caption(f"last check: {st.session_state.health['summary']}")

"""Playground: call any registered Voyager endpoint.

The endpoint picker and the raw builder both submit through `submit`, so a
response stays on screen until it is explicitly cleared. Reading the response
out of the button branch instead meant the next interaction deleted it — and
with it the Table/JSON toggle, which made the JSON view unreachable.
"""

import json

import pandas as pd
import streamlit as st

from admin_panel.registry import ALL_ENDPOINTS, AUTH_LABELS
from admin_panel.state import build_client, render_result, sidebar, submit


def render_param(p: dict, scope: str):
    """One registry param as a widget. `scope` namespaces the widget key.

    The key must include the endpoint: the same param name is a 3-option
    selectbox on the statement endpoints and a checkbox on /financials, and
    sharing one key leaks the previous endpoint's value into the next.
    """
    ptype = p.get("type", "text")
    label = p.get("label", p["name"])
    key = f"pg_{scope}_{p['name']}"
    help_text, required = p.get("help"), p.get("required", False)
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
        return st.checkbox(
            label, value=bool(p.get("default", False)), key=key, help=help_text
        )
    if ptype == "bool3":
        options = p.get("options", [])
        chosen = st.selectbox(label, [o["label"] for o in options], key=key, help=help_text)
        return next(o["value"] for o in options if o["label"] == chosen)
    if ptype == "select":
        options = p.get("options", [])
        default = p.get("default")
        index = options.index(default) if default in options else 0
        return st.selectbox(label, options, index=index, key=key, help=help_text)
    return st.text_input(label, key=key, help=help_text)


def playground() -> None:
    sidebar()
    client = build_client()
    st.subheader("Playground")
    st.caption("Call any Voyager endpoint. Endpoint list mirrors the API surface.")

    mode = st.radio(
        "Mode", ["Endpoint picker", "Raw request"], horizontal=True, key="inp_pg_mode"
    )
    if mode == "Endpoint picker":
        _picker(client)
    else:
        _raw(client)

    st.divider()
    st.markdown("**Request history** (this session)")
    hist = pd.DataFrame(st.session_state.history)
    if hist.empty:
        st.caption("No requests yet.")
        return
    st.dataframe(hist, width="stretch", hide_index=True)
    if st.button("🗑 Clear history"):
        st.session_state.history = []
        st.rerun()


def _picker(client) -> None:
    labels = [f"{e['group']} · {e['name']}  ({e['method']} {e['path']})" for e in ALL_ENDPOINTS]
    chosen = st.selectbox(
        "Endpoint", labels, key="inp_pg_endpoint", label_visibility="collapsed"
    )
    index = labels.index(chosen)
    ep = ALL_ENDPOINTS[index]
    st.markdown(f"**{ep['method']} `{ep['path']}`** · auth: `{AUTH_LABELS.get(ep['auth'])}`")
    if ep.get("description"):
        st.caption(ep["description"])

    with st.form("pg_picker", border=False):
        path = ep["path"]
        params = {}
        for p in ep.get("params", []):
            val = render_param(p, f"e{index}")
            if p.get("in_path"):
                path = path.replace("{" + p["name"] + "}", str(val) if val else "…")
            elif val is not None and val != "" and not (p["type"] == "bool3" and val == "null"):
                params[p["name"]] = val

        body = None
        if ep.get("body") is not None:
            raw = st.text_area(
                "Request body (JSON)",
                value=json.dumps(ep["body"], indent=2),
                height=180,
                key=f"pg_body_e{index}",
            )
            try:
                body = json.loads(raw)
            except ValueError as exc:
                st.error(f"Invalid JSON body: {exc}")
                body = None

        execute = st.form_submit_button("▶️ Execute", type="primary", width="stretch")

    if execute:
        if body is None and ep.get("body") is not None:
            st.error("Fix the JSON body before executing.")
        else:
            submit(
                "pg_result", ep["method"], path,
                client=client, params=params or None, body=body,
                admin=ep["auth"] == "admin", timeout=120,
            )
    render_result("pg_result", name="pg")


def _raw(client) -> None:
    c1, c2 = st.columns([1, 3])
    with st.form("pg_raw", border=False):
        method = c1.selectbox("Method", ["GET", "POST", "DELETE", "PUT", "PATCH"], key="inp_pg_method")
        path = c2.text_input("Path", value="/financial-metrics", key="inp_pg_path")
        params_text = st.text_area(
            "Query params (one `key=value` per line)", height=90, key="inp_pg_raw_params"
        )
        body_text = st.text_area("Body (JSON, optional)", height=90, key="inp_pg_raw_body")
        admin = st.checkbox("Use admin key (X-Voyager-Admin-Key)", key="inp_pg_raw_admin")
        execute = st.form_submit_button("▶️ Execute raw", type="primary")

    if not execute:
        render_result("pg_result", name="pg")
        return

    params = {}
    for line in params_text.splitlines():
        line = line.strip()
        if "=" in line:
            k, _, v = line.partition("=")
            params[k.strip()] = v.strip()
    body = None
    if body_text.strip():
        try:
            body = json.loads(body_text)
        except ValueError as exc:
            st.error(f"Invalid JSON body: {exc}")
            body = None
    if body is not None or not body_text.strip():
        submit(
            "pg_result", method, path,
            client=client, params=params or None, body=body,
            admin=admin, timeout=120,
        )
    render_result("pg_result", name="pg")

"""API Keys: create, list, revoke and re-enable service keys.

The raw key is shown exactly once by the server, so it is held in
session_state until it is explicitly dismissed — rendering it out of the
button branch loses it on the next interaction, which is unrecoverable.
Revoke is destructive and irreversible, so it goes through a confirm dialog.
"""

import pandas as pd
import streamlit as st

from admin_panel.state import (
    banner_for_error,
    build_client,
    current_cfg,
    sidebar,
    submit,
)

SCOPES = ["data:read", "data:write", "admin"]
RPM_MAX = 10000


@st.dialog("Revoke this key?")
def _confirm_revoke(client, prefix: str) -> None:
    st.write(f"`{prefix}` stops working immediately. There is no undo.")
    c1, c2 = st.columns(2)
    if c1.button("Revoke", type="primary", width="stretch"):
        submit("key_action", "DELETE", f"/admin/keys/{prefix}", client=client, admin=True, timeout=30)
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()


def keys() -> None:
    sidebar()
    client = build_client()
    st.subheader("API Keys")
    if not current_cfg().admin_key:
        st.warning("Set the admin key (VOYAGER_ADMIN_KEY) in the sidebar to manage keys.")
        return

    _new_key(client)
    st.divider()
    _list(client)


def _new_key(client) -> None:
    st.markdown("**Create key**")
    with st.form("key_create", border=False):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", key="key_name", placeholder="my-app")
        owner = c2.text_input("Owner", key="key_owner", placeholder="optional")
        scopes = st.multiselect("Scopes", SCOPES, default=["data:read"], key="key_scopes")
        c3, c4 = st.columns(2)
        rpm = c3.number_input("RPM", min_value=1, max_value=RPM_MAX, value=60, step=1, key="key_rpm")
        expires = c4.number_input(
            "Expires in days (0 = never)", min_value=0, value=0, step=1, key="key_expires"
        )
        create = st.form_submit_button("➕ Create key", type="primary")

    if create:
        if not name.strip():
            st.warning("Name is required.")
        else:
            body = {
                "name": name,
                "owner": owner,
                "scopes": scopes or ["data:read"],
                "rpm": int(rpm),
            }
            if int(expires) > 0:
                body["expires_in_days"] = int(expires)
            submit("new_key", "POST", "/admin/keys", client=client, body=body, admin=True, timeout=30)

    pending = st.session_state.get("new_key")
    if not pending:
        return
    if pending["err"] is not None:
        st.error(f"HTTP {pending['err'].status_code}: {pending['err'].detail}")
        return
    raw = (pending["resp"].json or {}).get("key")
    if not raw:
        st.error("The server did not return a key.")
        return
    st.success("Key created — copy it now, the server won't show it again.")
    st.code(raw, language="text")
    c1, c2 = st.columns([1, 1])
    c1.download_button(
        "⬇️ Download key", raw.encode(), file_name=f"{name}-key.txt",
        mime="text/plain", key="key_download", width="stretch",
    )
    if c2.button("✓ I've copied it", key="key_dismiss", width="stretch"):
        st.session_state.new_key = None
        st.session_state.key_list = None  # force a reload so the new key appears
        st.rerun()


def _list(client) -> None:
    st.markdown("**List & manage**")
    # Load on demand rather than on every render: this is an admin-authenticated
    # call, and the old code issued one per widget interaction app-wide.
    refresh = st.button("🔄 Refresh list")
    if st.session_state.get("key_list") is None or refresh:
        submit("key_list", "GET", "/admin/keys", client=client, admin=True, timeout=30)

    result = st.session_state.get("key_list")
    if not result:
        return
    if result["err"] is not None:
        banner_for_error(result["err"], True)
        return

    keys = result["resp"].json or []
    stamp = st.session_state.key_list.get("at")
    st.caption(f"{len(keys)} key(s) · loaded {stamp}")
    if not keys:
        st.caption("No keys yet.")
        return

    df = pd.DataFrame(keys)
    cols = [
        c
        for c in (
            "name", "owner", "prefix", "scopes", "rpm", "enabled",
            "expires_at", "last_used_at", "created_at", "revoked_at",
        )
        if c in df.columns
    ]
    st.dataframe(df[cols], width="stretch", hide_index=True)

    st.markdown("**Actions**")
    known = [k["prefix"] for k in keys if k.get("prefix")]
    if not known:
        return
    a1, a2 = st.columns(2)
    prefix = a1.selectbox("Key (prefix)", known, key="key_act_prefix")
    action = a2.selectbox("Action", ["revoke", "enable"], key="key_act_type")
    if st.button("Apply"):
        if action == "revoke":
            _confirm_revoke(client, prefix)
        else:
            submit(
                "key_action", "POST", f"/admin/keys/{prefix}/enable",
                client=client, admin=True, timeout=30,
            )
            st.rerun()

    action_result = st.session_state.get("key_action")
    if action_result and action_result["err"] is not None:
        st.error(f"HTTP {action_result['err'].status_code}: {action_result['err'].detail}")
    elif action_result:
        st.success(f"{action} {prefix}: {(action_result['resp'].json or {}).get('status')}")
        st.session_state.key_list = None  # the list is stale now; refetch next render
        st.rerun()

"""API Keys: create, list, revoke and re-enable service keys (direct DB).

The HTTP admin router is gone; this page talks to PostgreSQL through
admin_panel.keys_db (same key rules as the auth package).

The raw key is shown exactly once by create, so it renders in the button
branch — anything persisted in session_state can be lost on the next
interaction, which is unrecoverable. Revoke is destructive and
irreversible, so it goes through a confirm dialog.
"""

import pandas as pd
import streamlit as st

from admin_panel import keys_db
from admin_panel.db_stats import DBError
from admin_panel.state import current_cfg, sidebar

SCOPES = ["data:read", "data:write", "admin"]
RPM_MAX = 10000


def _engine():
    engine = st.session_state.get("keys_engine")
    if engine is None:
        engine = keys_db.connect(current_cfg().database_url)
        st.session_state.keys_engine = engine
    return engine


@st.dialog("Revoke this key?")
def _confirm_revoke(prefix: str) -> None:
    st.write(f"`{prefix}` stops working immediately. There is no undo.")
    c1, c2 = st.columns(2)
    if c1.button("Revoke", type="primary", width="stretch"):
        try:
            keys_db.revoke_key(_engine(), prefix)
            st.success(f"revoked {prefix}")
        except DBError as e:
            st.error(str(e))
        st.session_state.key_list = None
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()


def keys() -> None:
    sidebar()
    st.subheader("API Keys")
    if not current_cfg().database_url:
        st.warning("Set the database URL (DATABASE_URL) in the sidebar to manage keys.")
        return

    _new_key()
    st.divider()
    _list()


def _new_key() -> None:
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
            try:
                _, raw = keys_db.create_key(
                    _engine(),
                    name=name,
                    owner=owner,
                    label=None,
                    scopes=scopes or ["data:read"],
                    rpm=int(rpm),
                    expires_in_days=int(expires),
                )
            except DBError as e:
                st.error(str(e))
                return
            st.success("Key created — copy it now, it won't be shown again.")
            st.code(raw, language="text")
            c1, c2 = st.columns([1, 1])
            c1.download_button(
                "⬇️ Download key", raw.encode(), file_name=f"{name}-key.txt",
                mime="text/plain", key="key_download", width="stretch",
            )


def _list() -> None:
    st.markdown("**List & manage**")
    if st.button("🔄 Refresh list"):
        st.session_state.key_list = None

    if st.session_state.get("key_list") is None:
        try:
            st.session_state.key_list = keys_db.list_keys(_engine())
        except DBError as e:
            st.error(str(e))
            return

    keys = st.session_state.key_list
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
            _confirm_revoke(prefix)
        else:
            try:
                keys_db.enable_key(_engine(), prefix)
                st.success(f"enabled {prefix}")
            except DBError as e:
                st.error(str(e))
            st.session_state.key_list = None
            st.rerun()

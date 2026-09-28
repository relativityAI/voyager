"""Voyager admin panel — multipage Streamlit UI.

Run:  streamlit run admin_panel/app.py

One page per module under admin_panel/pages/. Only the page you are looking
at executes, so a keystroke in the Playground no longer fires an
admin-authenticated call from the API Keys tab.
"""

import streamlit as st

from admin_panel.pages.database import database
from admin_panel.pages.keys import keys
from admin_panel.pages.metrics import metrics
from admin_panel.pages.overview import overview
from admin_panel.pages.playground import playground
from admin_panel.pages.pulls import pulls
from admin_panel.state import init_state

st.set_page_config(page_title="Voyager Admin", page_icon="🛰️", layout="wide")

init_state()

st.navigation(
    [
        st.Page(overview, title="Overview", icon="🛰️", default=True),
        st.Page(pulls, title="Pull Manager", icon="📥"),
        st.Page(playground, title="Playground", icon="▶️"),
        st.Page(database, title="Database Stats", icon="🗄️"),
        st.Page(keys, title="API Keys", icon="🔑"),
        st.Page(metrics, title="Metrics", icon="📈"),
    ],
    position="sidebar",
).run()

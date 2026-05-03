import streamlit as st
import requests
import pandas as pd
from config import BASE_URL

st.title("🛢 VMI DB Viewer")
st.divider()

st.markdown("#### Raw Financial Viewer ")
# --- Category selection ---
tabs = ["Results", "Announcements", "Shareholdings", "Annual Reports"]
current_tab = st.selectbox("Select Category", tabs)

# --- Initialize session state for all tabs ---
for tab in tabs:
    data_key = f"{tab.lower()}_data"
    stats_key = f"{tab.lower()}_stats"
    if data_key not in st.session_state:
        st.session_state[data_key] = None
    if stats_key not in st.session_state:
        st.session_state[stats_key] = ""

# --- Inputs in one row ---
col1, col2, col3, col4, col5 = st.columns(5)
symbol = col1.text_input("Ticker", "YATHARTH", key=f"{current_tab}_symbol")
exchange = col2.text_input("Exchange", "NSE", key=f"{current_tab}_exchange")
period = col3.selectbox("Period", ['quarterly', 'annual'], key=f"{current_tab}_period")
from_date = col4.date_input("From Date", value=None, key=f"{current_tab}_from_date")
to_date = col5.date_input("To Date", value=None, key=f"{current_tab}_to_date")

# --- Keys input for Results and Shareholdings ---
keys = []
if current_tab in ["Results", "Shareholdings"]:
    keys = st.text_area("Keys (comma separated)", value="RevenueFromOperations", key=f"{current_tab}_keys").split(",")

# --- Fetch button ---
if st.button("Fetch Data", key=f"{current_tab}_fetch"):
    params = {
        "symbol": symbol,
        "exchange": exchange,
        "from_date": str(from_date) if from_date else None,
        "to_date": str(to_date) if to_date else None,
    }

    if current_tab in ["Results", "Shareholdings"]:
        params['period'] = period

    if keys:
        params["filter_keys"] = [k.strip() for k in keys if k.strip()]

    endpoint_map = {
        "Results": "results",
        "Announcements": "announcements",
        "Shareholdings": "shareholdings",
        "Annual Reports": "annual_reports"
    }
    endpoint = endpoint_map[current_tab]

    response = requests.get(f"{BASE_URL}/{endpoint}", params=params)
    if response.status_code == 200:
        data = response.json()
        if data:
            df = pd.DataFrame(data)
            st.session_state[f"{current_tab.lower()}_data"] = df

            # Compute stats
            total_rows = len(df)
            total_cols = len(df.columns)
            total_memory = df.memory_usage(deep=True).sum()  # in bytes
            avg_memory_per_col = total_memory / total_cols if total_cols > 0 else 0

            # Convert to KB and MB
            total_kb = total_memory / 1024
            total_mb = total_kb / 1024
            avg_kb = avg_memory_per_col / 1024
            avg_mb = avg_kb / 1024

            # Prepare markdown string and store in session state
            stats_md = (
                f"**Rows:** `{total_rows}` | **Columns:** `{total_cols}`  \n"
                f"**Total size:** `{total_kb:.2f} KB / {total_mb:.2f} MB`  \n"
                f"**Avg per column:** `{avg_kb:.2f} KB / {avg_mb:.2f} MB`"
            )
            st.session_state[f"{current_tab.lower()}_stats"] = stats_md
        else:
            st.info(f"No {current_tab.lower()} data found.")
            st.session_state[f"{current_tab.lower()}_data"] = None
            st.session_state[f"{current_tab.lower()}_stats"] = ""
    else:
        st.error(f"Error fetching {current_tab.lower()}: {response.status_code}")

# --- Display stored stats ---
st.divider()

if st.session_state[f"{current_tab.lower()}_stats"]:
    st.markdown(st.session_state[f"{current_tab.lower()}_stats"])

# --- Display stored data ---
if st.session_state[f"{current_tab.lower()}_data"] is not None:
    st.dataframe(st.session_state[f"{current_tab.lower()}_data"])

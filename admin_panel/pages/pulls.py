import streamlit as st

st.title("Data Ingestion")
st.info(
    "Ingestion runs from the CLI (cron), not the API: "
    "Ingest via the `src.ingest` package (see README → Data ingestion)."
)

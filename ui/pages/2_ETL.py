import streamlit as st
import requests

from config import BASE_URL

st.title("⬇️ Download and Process Data")
st.markdown(f"""### Data processing includes
- Calculation and insertion of financial ratios
- Document extraction""")

status_list = ['download_status', "fundamentals_status", "valuations_status"]
for x in status_list:
    if x not in st.session_state:
        st.session_state[x] = None



st.divider()

st.markdown("#### 🌐 Scrape data")
col1, col2, col3 = st.columns([2,2, 1], vertical_alignment="bottom")
symbol = col1.text_input("Enter Symbol", "YATHARTH")
data_type = col2.selectbox("Select Data Type", ["announcements", "results", "shareholding", "annual_report"])

if col3.button("Download Data"):
    payload = {"symbol": symbol, "data_type": data_type}
    response = requests.post(f"{BASE_URL}/download", json=payload)
    if response.status_code == 200:
        st.session_state["download_status"] = f"{data_type} data downloaded successfully!"
    else:
        st.session_state["download_status"] = f"Error: {response.status_code}"

if st.session_state["download_status"]:
    st.info(st.session_state["download_status"])

st.divider()

st.markdown("#### ⚙️ Caluate ratios")

col1, col2 = st.columns([1,1], vertical_alignment='bottom')

symbols = col1.text_input("Enter symbols (comma seperated)")
symbols = [ x.strip().upper() for x in symbols.split(",")  ]
period = col2.selectbox("Select Period", ['quarterly', 'annual'])

payload = {
    'symbols' : symbols,
    'period' : period

}

col1, col2 = st.columns([0.2,1], vertical_alignment='center')
col1.markdown("**Fundamentals**")
if col2.button("Execute Fundamentals Pipeline"):
    with st.spinner("Executing...", show_time=True):
        response = requests.post(f"{BASE_URL}/process_fundamentals", json=payload)
        if response.status_code == 200:
            st.session_state["fundamentals_status"] = f"Fundamentals processed successfully!"
        else:
            st.session_state["fundamentals_status"] = f"Error: {response.status_code}"

        if st.session_state["fundamentals_status"]:
            st.info(st.session_state["fundamentals_status"])

col1.markdown("**Valuations - Time Series**")

if col2.button("Execute Valuations Pipeline"):

    with st.spinner("Executing...", show_time=True):
        response = requests.post(f"{BASE_URL}/process_valuations", json=payload)
        if response.status_code == 200:
            st.session_state["valuations_status"] = f"Valuations processed successfully!"
        else:
            st.session_state["valuations_status"] = f"Error: {response.status_code}"

        if st.session_state["valuations_status"]:
            st.info(st.session_state["valuations_status"])


st.divider()

st.markdown("#### 📄 Document extraction")
if "extracted_text" not in st.session_state:
    st.session_state["extracted_text"] = None

col1, col2 = st.columns([4,1], vertical_alignment="bottom")
url = col1.text_input("Enter Document URL", "")

if col2.button("Extract"):
    response = requests.get(f"{BASE_URL}/extract?url={url}")
    if response.status_code == 200:
        st.session_state["extracted_text"] = response.text
    else:
        st.session_state["extracted_text"] = f"Error: {response.status_code}"

if st.session_state["extracted_text"]:
    st.text_area("Extracted Text", json.loads(st.session_state["extracted_text"]), height=700)

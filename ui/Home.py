import streamlit as st
import requests
from config import BASE_URL

st.set_page_config(page_title="VMI Admin Portal", layout="wide")

# --- HEADER ---
col1, col2 = st.columns([1,2], vertical_alignment="center")
col1.title("Voyager Market Intelligence - Admin Panel")
col2.image(
    "https://upload.wikimedia.org/wikipedia/commons/thumb/6/60/Voyager_spacecraft_model.png/1200px-Voyager_spacecraft_model.png",
    width = 200
    )

OPENAPI_URL = f"{BASE_URL}/openapi.json"

# --- FETCH OPENAPI SPEC ---


try:
    if 'openapi' not in st.session_state:
        resp = requests.get(OPENAPI_URL)
        resp.raise_for_status()
        data = resp.json()
        st.session_state['openapi'] = data
except Exception as e:
    st.error(f"Failed to fetch OpenAPI spec: {e}")
    st.stop()

st.markdown("#### API Details")
st.markdown(f"""
Base Endpoints: **`{BASE_URL}`**   
VMI version: `{st.session_state['openapi']['info']['version']}`  
API Title: `{st.session_state['openapi']['info']['title']}`  
Description: `{st.session_state['openapi']['info']['description']}`
  
OpenAPI version: `{st.session_state['openapi']['openapi']}`  
""")
st.divider()


# --- STYLE HELPERS ---
def method_badge(method: str) -> str:
    colors = {
        "GET": "green",
        "POST": "blue",
        "PUT": "orange",
        "DELETE": "red",
        "PATCH": "purple",
    }
    color = colors.get(method.upper(), "gray")
    return f"<span style='background-color:{color};color:white;padding:2px 6px;border-radius:6px;font-size:0.85em'>{method.upper()}</span>"

# --- DISPLAY ENDPOINTS ---
st.subheader("API Endpoints")

for path, methods in st.session_state['openapi']["paths"].items():
    with st.expander(f"🔗 `{path}`"):
        for method, details in methods.items():
            st.markdown(
                f"{method_badge(method)} &nbsp; **{details.get('summary', 'No summary')}**",
                unsafe_allow_html=True
            )
            if "description" in details:
                st.write(details["description"])
            if "parameters" in details:
                st.markdown("**Parameters:**")
                for p in details["parameters"]:
                    st.markdown(f"- `{p['name']}` ({p.get('in', 'query')}) - {p.get('description', '')}")
            st.markdown("---")

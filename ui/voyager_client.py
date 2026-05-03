import requests

API_BASE = "http://localhost:8001"

def get_symbols():
    return requests.get(f"{API_BASE}/symbols").json()

def scrape_sources(symbol, category):
    return requests.post(f"{API_BASE}/sources", json={"symbol": symbol, "category": category})

def extract_document(url, symbol, category):
    return requests.post(
        f"{API_BASE}/extract-doc", 
        json={
            "url": url, 
            "symbol": symbol, 
            "category": category
            }
    )

def extract_category(symbol, category):
    return requests.post(
        f"{API_BASE}/extract-category", 
        json={
            "symbol": symbol, 
            "category": category
            }
    )

def read_data(endpoint, symbol):
    resp = requests.get(f"{API_BASE}/{endpoint}", params={"symbol": symbol})
    return resp.json()

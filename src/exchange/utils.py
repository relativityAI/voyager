from src.utils import console
from io import BytesIO
from lxml import etree
import pandas as pd
import requests

from fake_useragent import UserAgent

from src.utils import console

def get_fake_user_agent():
    try:
        return UserAgent().random
    except Exception as e:
        console.log(f"[bold red]Failed to get fake user agent")
        console.log(f"[red]{e}")
        return None

def get_headers(exchange:str = 'nse'):
    f_u_a = get_fake_user_agent()
    if not f_u_a:
        f_u_a = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    return {
        "User-Agent" : f_u_a,
        "Accept": "application/json, text/plain, */*",
        "Connection": "keep-alive",
        "Referer": "https://www.nseindia.com/" if exchange=="nse" else "https://www.bseindia.com/",
    }

def parse_xbrl(xbrl_content : BytesIO, get_json=False,  display=False):
    if display:
        console.log(f"🔍 Parsing XBRL content...")
    tree = etree.parse(xbrl_content)
    root = tree.getroot()

    nsmap = root.nsmap.copy()
    if None in nsmap:
        nsmap['default'] = nsmap.pop(None)

    rows = []
    for elem in root.iter():
        tag = etree.QName(elem.tag).localname
        ns = etree.QName(elem.tag).namespace
        if tag in ('context', 'unit', 'xbrl'):
            continue
        text = elem.text.strip() if elem.text else None
        if ns and text:
            rows.append({
                'namespace': ns,
                'tag': tag,
                'value': text,
                'contextRef': elem.get('contextRef'),
                'unitRef': elem.get('unitRef'),
                'decimals': elem.get('decimals')
            })

    if get_json:
        return rows

    df = pd.DataFrame(rows)
    if display:
        console.log(f"✅ Extracted {len(df)} facts ")
    return df

def parse_html_nse(response :requests.Response):
    # For HTML quarterly results 
    attr_map_path = os.path.join(this_dir, "attr_map.json")
    with open(attr_map_path) as f:
        replacement_dict = json.load(f)

    item:dict = {}

    match_threshold = 80  # Minimum fuzzy match score to accept replacement
    table_id = 5

    soup = BeautifulSoup(response.content, "html.parser")
    tables = pd.read_html(StringIO(str(soup)))

    if len(tables) < 2:
        raise ValueError("Less than 2 tables found in the HTML.")

    df = tables[table_id] 

    # --- Step 2: Dynamically get 2nd column ---
    first_col = df.columns[0]
    second_col = df.columns[1]

    # --- Step 3: Fuzzy match and replace values ---
    def fuzzy_replace(value, mapping_dict):
        match, score = process.extractOne(str(value), mapping_dict.keys(), scorer=fuzz.token_set_ratio)
        return mapping_dict[match] if score >= match_threshold else value

    df[first_col] = df[first_col].apply(lambda x: fuzzy_replace(x, replacement_dict)) # convert terminology
    df = df[pd.to_numeric(df[second_col], errors='coerce').notnull()] # remove non numeric values
    df[second_col] = df[second_col].astype(float)

    # Except EPS, multiple the Crore factor
    df.loc[~df[first_col].isin(['BasicEarningsLossPerShareFromContinuingOperations', 'DilutedEarningsLossPerShareFromContinuingOperations']), second_col] = df[second_col] * 100000

    df = df.sort_values(second_col, ascending=False) # sort values col
    repl_values = list(replacement_dict.values())
    filtered_df = df[df[first_col].isin(repl_values)].drop_duplicates(subset=[first_col], keep='first').reset_index(drop=True)

    first_col = filtered_df.columns[0]
    second_col = filtered_df.columns[1]

    for idx, _ in filtered_df.iterrows():
        tag = filtered_df.loc[idx, first_col]
        value = filtered_df.loc[idx, second_col]
        item[tag] = value

    return item


"""
if __name__ == "__main__":
    base_url = "https://www.nseindia.com"
    xbrl_url = "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1496535_29072025044446_WEB.xml"
    xbrl_file = "integrated.xml"
    output_csv = "integrated_facts.csv"

    download_xbrl_with_session(base_url, xbrl_url, xbrl_file)
    parse_xbrl(xbrl_file, output_csv)
"""
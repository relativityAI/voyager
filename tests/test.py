from src.utils import console
from pprint import pprint
"""
def test_xbrl_parsing():
    from src.scraper.xbrl_parsing import fetch_xbrl_nse, parse_xbrl
    from src.utils import write_json
    import pandas as pd

    test_save_path = "test.csv"
    import os

    if os.path.exists(test_save_path):
        print("exists")
        df = pd.read_csv(test_save_path)
    else:
        xbrl = fetch_xbrl_nse()
        df = parse_xbrl(xbrl)
        df.to_csv(test_save_path, index=False)

    write_json("test.json", df.to_dict(orient="records"))
"""
def test_api_call():
    from src.exchange.nse import NSEIndia
    nseindia = NSEIndia()

    console.print(nseindia.announcements_xbrls("SKYGOLD"))

def test_db_write_n_read():
    from src.data.manager import DataManager
    import pandas as pd
    dm = DataManager()

    # ids = dm.download_announcements("YATHARTH")
    # console.log(ids)

    data = dm.read_announcements(
        symbol="YATHARTH", 
        from_date="2025-08-01",
        to_date = "2025-08-30"
        )
    df = pd.DataFrame(data)
    print(df[['desc', 'sort_date']])

def test_download_financials():
    from src.data.manager import DataManager
    import pandas as pd
    dm = DataManager()

    ids = dm.download_results("YATHARTH")
    # console.log(ids)

def test_read_financials():
    from src.data.manager import DataManager
    import pandas as pd
    dm = DataManager()

    # ids = dm.download_quarterly_results("YATHARTH")
    data = dm.read_results(symbol="YATHARTH", filter_keyword="RevenueFromOperations")
    df = pd.DataFrame(data)
    console.log(df.iloc[0].to_dict())
    console.log(df)
    df.to_csv("test_revenue.csv")

def test_download_shareholdings():
    from src.data.manager import DataManager
    import pandas as pd
    dm = DataManager()

    df = dm.download_shareholdings("YATHARTH", [] )
    print(df)
    # df.to_csv("test_shareholding.csv", index=False)


def text_doc_extraction(url: str):
    from src.data.manager import DataManager
    import pandas as pd
    dm = DataManager()

    text = dm.extract(url)
    console.log(text)
    
def test_ratios_calculations():
    from src.pipelines import calculate_symbol_ratios_n_insert_to_db, ratios_pipeline
    # calculate_symbol_ratios_n_insert_to_db()
    ratios_pipeline(['KPITTECH'])
    # pprint(calculate_symbol_ratios_n_insert_to_db())

# test_api_call()
# test_db_write_n_read()

# test_read_financials()
# test_download_shareholdings()
# test_download_financials()

# text_doc_extraction("https://www.bseindia.com/xml-data/corpfiling/AttachHis/cb907940-d06e-4cc4-b061-67763ce2cdaf.pdf")
# text_doc_extraction("https://nsearchives.nseindia.com/corporate/KEI_29072025184846_SignedIntimation.pdf")


# test_ratios_calculations()

from cli import *
import inspect

def test_cli_to_api():

    print(inspect.getsource(screener_download))    
    print("-------------")
    print(inspect.signature(screener_download))    
    print(inspect.signature(screener_download).parameters['api'].default)    
    # print(dir(inspect.signature(screener_download)))    
    print("-------------")
    print(inspect.getdoc(screener_download))    
    print("-------------")
    print("-------------")
    
    pass

if __name__ == "__main__":
    
    # test_cli_to_api()
    
    pass

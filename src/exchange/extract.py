import pandas as pd
from src.exchanges.extract_utils import extract_text_from_pdf
from src.exchanges.nse import NSEIndia

_nseindia = NSEIndia()


def nse_api_call(url: str, symbol: str):
    return _nseindia.api_call(url=url, symbol=symbol)


def extract_xml_or_html(url: str, symbol: str) -> pd.DataFrame:
    return _nseindia._extract_xml_or_html(url=url, symbol=symbol)


def extract_pdf_bse(nse_response, symbol: str):
    text = extract_text_from_pdf(response=nse_response)

    if text and isinstance(text, str):
        return text
    return None


def extract_pdf_bse():
    raise NotImplementedError("BSE PDF extraction is not implemented yet.")

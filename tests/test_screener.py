import pytest
from unittest.mock import MagicMock, patch
from src.tools.web_screeners.screener import Screener
from datetime import datetime
import pandas as pd

@pytest.fixture
def screener():
    return Screener()

def test_clean(screener):
    assert screener.clean("  Reliance Industries  ") == "Reliance Industries"
    assert screener.clean("!!!NSE:RELIANCE!!!") == "NSE:RELIANCE"

def test_generate_url(screener):
    assert screener.generate_url("RELIANCE") == "https://www.screener.in/company/RELIANCE/consolidated/"
    assert screener.generate_url("RELIANCE", consolidated=False) == "https://www.screener.in/company/RELIANCE"

def test_process_date(screener):
    # Test TTM
    assert screener.process_date("TTM") == "TTM"
    
    # Test DD Mon YYYY
    assert screener.process_date("31 Mar 2024") == "2024-03-31"
    
    # Test Mon YYYY (last day of month)
    assert screener.process_date("Mar 2024") == "2024-03-31"
    
    # Test DD Mon (current year)
    current_year = datetime.now().year
    expected = f"{current_year}-03-31"
    assert screener.process_date("31 Mar") == expected

@patch("requests.get")
@patch("pandas.read_html")
@patch("src.tools.screener.BeautifulSoup")
def test_scrape_mock(mock_bs, mock_read_html, mock_get, screener):
    # Mock requests.get to avoid real network call
    mock_resp = MagicMock()
    mock_resp.content = b"<html></html>"
    mock_get.return_value = mock_resp
    
    # Mock pd.read_html to return an empty list of dataframes
    mock_read_html.return_value = []
    
    # Mock BeautifulSoup for the ratios/about section
    mock_soup = MagicMock()
    mock_bs.return_value = mock_soup
    
    # Setup mock returns for BeautifulSoup elements
    mock_soup.find.return_value.find.return_value.find_all.return_value = [] # ratios
    mock_soup.find.return_value.find.return_value.get_text.return_value = "About text" # about
    mock_soup.find.return_value.find_all.return_value = [] # annual-reports or credit-ratings
    
    result = screener.scrape("RELIANCE")
    assert isinstance(result, dict)
    assert "ratios" in result
    assert "about" in result

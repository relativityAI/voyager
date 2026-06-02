import pytest
from unittest.mock import MagicMock, patch
from src.tools.web_screeners.screener import Screener
from src.utils.rate_limiter import get_rate_limiter, reset_rate_limiters
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


class TestScreenerRateLimiter:
    """Test rate limiter integration with Screener."""

    def test_screener_initialization_with_rate_limit(self):
        """Test Screener initializes with configurable rate limit."""
        screener = Screener(calls_per_second=5)
        assert screener.rate_limiter is not None
        assert screener.rate_limiter.calls_per_second == 5

    def test_screener_default_rate_limit(self):
        """Test Screener uses default rate limit of 10 calls per second."""
        reset_rate_limiters()
        screener = Screener()
        assert screener.rate_limiter.calls_per_second == 10

    def test_screener_has_rate_limited_session(self):
        """Test Screener has a rate-limited session."""
        screener = Screener(calls_per_second=10)
        assert screener.session is not None

    def test_multiple_screener_instances_share_rate_limiter(self):
        """Test multiple Screener instances share the same rate limiter."""
        reset_rate_limiters()
        screener1 = Screener(calls_per_second=10)
        screener2 = Screener(calls_per_second=10)
        
        # They should share the same rate limiter instance
        assert screener1.rate_limiter is screener2.rate_limiter

    @patch("src.tools.web_screeners.screener.RateLimitedSession.get")
    def test_scrape_uses_rate_limited_session(self, mock_session_get):
        """Test that scrape method uses the rate-limited session."""
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        mock_session_get.return_value = mock_response
        
        screener = Screener()
        screener.scrape("RELIANCE")
        
        # Verify the rate-limited session was used
        assert mock_session_get.called

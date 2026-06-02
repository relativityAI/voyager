import pytest
from unittest.mock import patch, MagicMock
from src.tools.web_screeners.stockscans import StockScans

@pytest.fixture
def mock_response():
    return {
        "table": [
            {
                "name": "Auto",
                "totalCompanies": 50
            }
        ]
    }

@patch("src.tools.stockscans.generate_fake_headers")
def test_stockscans_init(mock_headers):
    mock_headers.return_value = {"User-Agent": "test"}
    scanner = StockScans()
    assert scanner.headers == {"User-Agent": "test"}
    mock_headers.assert_called_once()

@patch("src.tools.stockscans.requests.post")
def test_fetch_scan_success(mock_post, mock_response, monkeypatch):
    scanner = StockScans()
    
    mock_resp_obj = MagicMock()
    mock_resp_obj.json.return_value = mock_response
    mock_resp_obj.raise_for_status.return_value = None
    mock_post.return_value = mock_resp_obj
    
    url = "https://example.com/api"
    payload = {"key": "value"}
    
    result = scanner.fetch_scan(url, payload)
    
    mock_post.assert_called_once_with(url, headers=scanner.headers, json=payload)
    
    assert isinstance(result, dict)
    assert "table" in result
    assert len(result["table"]) == 1
    assert result["table"][0]["name"] == "Auto"

@patch("src.tools.stockscans.requests.post")
def test_fetch_scan_request_exception(mock_post):
    import requests
    scanner = StockScans()
    
    mock_post.side_effect = requests.exceptions.RequestException("Network error")
    
    result = scanner.fetch_scan("http://url", {})
    assert result == {}

@patch("src.tools.stockscans.requests.post")
def test_fetch_scan_json_decode_error(mock_post):
    scanner = StockScans()
    
    mock_resp_obj = MagicMock()
    mock_resp_obj.json.side_effect = ValueError("Invalid JSON")
    mock_resp_obj.raise_for_status.return_value = None
    mock_post.return_value = mock_resp_obj
    
    result = scanner.fetch_scan("http://url", {})
    assert result == {}

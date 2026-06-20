import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from src.tools.exchange.nse import NSEApiClient, NSEDataParser, NSEIndia
from src.db.models import NSEFinancials

@pytest.fixture
def parser():
    return NSEDataParser()

@pytest.fixture
def api_client():
    return NSEApiClient(calls_per_second=100)

@pytest.fixture
def nse_india():
    return NSEIndia(calls_per_second=100)

def test_extract_xml_valid(parser):
    sample_xml = b"""<?xml version="1.0" encoding="utf-8"?>
    <xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2015-03-31/in-bse-fin">
        <in-bse-fin:RevenueFromOperations contextRef="OneD">1000</in-bse-fin:RevenueFromOperations>
        <in-bse-fin:endDate>2023-03-31</in-bse-fin:endDate>
    </xbrli:xbrl>
    """
    result = parser.extract_xml(sample_xml, "TCS")
    assert result is not None
    assert result["symbol"] == "TCS"
    assert result["date"] == "2023-03-31"
    assert len(result["financials"]) == 2
    assert result["financials"][0]["tag"] == "RevenueFromOperations"
    assert result["financials"][0]["value"] == "1000"

def test_extract_xml_invalid(parser):
    result = parser.extract_xml(b"invalid xml", "TCS")
    assert result is None

@patch('src.tools.exchange.nse.RateLimitedSession')
def test_api_client_fetch_xbrl_content(mock_session_class, api_client):
    mock_session = mock_session_class.return_value
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"fake content"
    mock_session.get.return_value = mock_response

    # Force inject the mocked session
    api_client.session = mock_session
    api_client.session.cookies = {"fake": "cookie"}

    content = api_client.fetch_xbrl_content("http://fake.url", "TCS")
    assert content == b"fake content"
    mock_session.get.assert_called()

import asyncio

def test_run_background_scrape(mock_job_status_class, mock_nse_financials_class, nse_india):
    import asyncio

    asyncio.run()

@patch('src.tools.exchange.nse.RateLimitedSession')
def test_api_client_json_decode_error(mock_session_class, api_client):
    mock_session = mock_session_class.return_value
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = Exception("JSON Decode Error")
    mock_session.get.return_value = mock_response

    # Force inject the mocked session
    api_client.session = mock_session
    api_client.session.cookies = {"fake": "cookie"}

    result = api_client.integrated_filing_xbrls("TCS")
    assert result == {}
    mock_session.get.assert_called()


@patch('src.tools.exchange.nse.RateLimitedSession')
def test_api_client_non_200_recovers(mock_session_class, api_client):
    mock_session = mock_session_class.return_value
    fail_response = MagicMock()
    fail_response.status_code = 401
    
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"data": "test_success"}
    
    # Return 401 on first call, 200 on second call
    mock_session.get.side_effect = [fail_response, success_response]

    api_client.session = mock_session
    api_client.session.cookies = {"fake": "cookie"}
    api_client._set_cookies = MagicMock()

    result = api_client.quarterly_results_xbrls("TCS")
    
    assert result == {"data": "test_success"}
    assert mock_session.get.call_count == 2


def test_nse_financials_schema():
    fields = NSEFinancials.model_fields
    assert "financials" in fields
    assert "broadcast_date" in fields
    assert fields["financials"].default_factory == list

def test_nse_financials_fetch():
    nseindia = NSEIndia()
    
    # "perform a test fetch in tests/test_nse for SKYGOLD Share"
    integrated = nseindia.api.integrated_filing_xbrls("SKYGOLD")
    integrated_data = integrated.get("data", []) if isinstance(integrated, dict) else []
    
    if not integrated_data:
        quarterly = nseindia.api.quarterly_results_xbrls("SKYGOLD")
        quarterly_data = quarterly.get("data", quarterly) if isinstance(quarterly, dict) else quarterly
        if not isinstance(quarterly_data, list):
            quarterly_data = []
        data_list = quarterly_data
        category = "quarterly"
    else:
        data_list = integrated_data
        category = "integrated"

    found_parsed = False
    for x in data_list:
        parsed_data = nseindia.process_xbrl(x, "SKYGOLD", category)
        if parsed_data:
            assert parsed_data["symbol"] == "SKYGOLD"
            assert "financials" in parsed_data
            assert "date" in parsed_data
            assert len(parsed_data["financials"]) > 0
            found_parsed = True
            break
            
    assert found_parsed, "Could not fetch and parse XBRL data for SKYGOLD"

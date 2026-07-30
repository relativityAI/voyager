import unittest

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from src.tools.nse.client import NSEApiClient, NSEDataParser, NSEIndia
# NSEFinancials model was removed; skip the test that imports it
try:
    from src.db.models import NSEFinancials
except ImportError:
    NSEFinancials = None

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
    assert result["period_end_date"] == "2023-03-31"
    assert len(result["financials"]) == 2
    assert result["financials"][0]["tag"] == "RevenueFromOperations"
    assert result["financials"][0]["value"] == "1000"

def test_extract_xml_invalid(parser):
    result = parser.extract_xml(b"invalid xml", "TCS")
    assert result is None

@patch('src.tools.nse.client.RateLimitedSession')
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

@patch('src.tools.nse.client.RateLimitedSession')
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


@patch('src.tools.nse.client.RateLimitedSession')
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
    if NSEFinancials is None:
        pytest.skip("NSEFinancials model not available")
    fields = NSEFinancials.model_fields
    assert "financials" in fields
    assert "broadcast_date" in fields
    assert fields["financials"].default_factory == list

SAMPLE_XBRL = b"""<?xml version="1.0" encoding="utf-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2015-03-31/in-bse-fin">
    <xbrli:context id="OneI">
        <xbrli:entity><xbrli:identifier scheme="http://www.bseindia.com">TEST</xbrli:identifier></xbrli:entity>
        <xbrli:period>
            <xbrli:startDate>2025-07-01</xbrli:startDate>
            <xbrli:endDate>2025-09-30</xbrli:endDate>
        </xbrli:period>
    </xbrli:context>
    <xbrli:context id="FourD">
        <xbrli:entity><xbrli:identifier scheme="http://www.bseindia.com">TEST</xbrli:identifier></xbrli:entity>
        <xbrli:period>
            <xbrli:startDate>2025-01-01</xbrli:startDate>
            <xbrli:endDate>2025-12-31</xbrli:endDate>
        </xbrli:period>
    </xbrli:context>
    <in-bse-fin:endDate>2025-09-30</in-bse-fin:endDate>
    <in-bse-fin:RevenueFromOperations contextRef="OneI">5000000</in-bse-fin:RevenueFromOperations>
    <in-bse-fin:RevenueFromOperations contextRef="FourD">15000000</in-bse-fin:RevenueFromOperations>
    <in-bse-fin:ProfitLossForPeriod contextRef="OneI">1000000</in-bse-fin:ProfitLossForPeriod>
    <in-bse-fin:ProfitLossForPeriod contextRef="FourD">3500000</in-bse-fin:ProfitLossForPeriod>
    <in-bse-fin:DilutedEarningsLossPerShareFromContinuingOperations contextRef="OneI">0.53</in-bse-fin:DilutedEarningsLossPerShareFromContinuingOperations>
    <in-bse-fin:DilutedEarningsLossPerShareFromContinuingOperations contextRef="FourD">2.10</in-bse-fin:DilutedEarningsLossPerShareFromContinuingOperations>
</xbrli:xbrl>
"""

MOCK_XBRL_URL = "https://nsearchives.nseindia.com/corporate/xbrl/test.xml"

def test_process_xbrl_filters_quarterly(nse_india):
    """process_xbrl should classify quarterly (OneD/OneI) facts into correct statement docs."""
    mock_record = {"xbrl": MOCK_XBRL_URL, "consolidated": "Consolidated"}
    with unittest.mock.patch.object(nse_india.api, "fetch_xbrl_content", return_value=SAMPLE_XBRL):
        result = nse_india.process_xbrl(mock_record, "TEST", "integrated-filing")
    assert result is not None
    assert result["income_statement"] is not None
    assert result["balance_sheet"] is None
    assert result["cash_flow"] is None
    assert result["shareholding"] is None
    doc = result["income_statement"]
    assert doc["symbol"] == "TEST"
    assert doc["period_end_date"] == "2025-09-30"
    assert doc["consolidated"] is True
    assert doc["revenue_from_operations"] == "5000000"
    assert doc["profit_loss_for_period"] == "1000000"
    assert doc["diluted_earnings_loss_per_share_from_continuing_operations"] == "0.53"


def test_process_xbrl_filters_annual(nse_india):
    """process_xbrl should only keep annual (FourD) facts for annual-results."""
    mock_record = {"xbrl": MOCK_XBRL_URL, "consolidated": "Consolidated"}
    with unittest.mock.patch.object(nse_india.api, "fetch_xbrl_content", return_value=SAMPLE_XBRL):
        result = nse_india.process_xbrl(mock_record, "TEST", "annual-results")
    assert result is not None
    assert result["income_statement"] is not None
    doc = result["income_statement"]
    assert doc["revenue_from_operations"] == "15000000"
    assert doc["profit_loss_for_period"] == "3500000"
    assert doc["diluted_earnings_loss_per_share_from_continuing_operations"] == "2.10"


def test_process_xbrl_skips_empty_after_filter(nse_india):
    """process_xbrl should return None when no facts match the filter."""
    sample = SAMPLE_XBRL.replace(b"2025-01-01", b"2025-07-01").replace(b"2025-12-31", b"2025-09-30")
    mock_record = {"xbrl": MOCK_XBRL_URL, "consolidated": "Consolidated"}
    with unittest.mock.patch.object(nse_india.api, "fetch_xbrl_content", return_value=sample):
        result = nse_india.process_xbrl(mock_record, "TEST", "annual-results")
    assert result is None, "Should skip XBRL with no annual duration facts"

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
        ep_key = "integrated-filing" if category == "integrated" else "quarterly-results"
        parsed_data = nseindia.process_xbrl(x, "SKYGOLD", ep_key)
        if parsed_data:
            stmt = parsed_data.get("income_statement") or parsed_data.get("balance_sheet") or parsed_data.get("cash_flow") or parsed_data.get("shareholding")
            assert stmt is not None, "At least one statement doc should be present"
            assert stmt["symbol"] == "SKYGOLD"
            assert "period_end_date" in stmt
            found_parsed = True
            break

    assert found_parsed, "Could not fetch and parse XBRL data for SKYGOLD"

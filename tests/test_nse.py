import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.session import BlockedResponse, CookieError, SessionExhausted
from src.tools.nse.client import NSEApiClient, NSEDataParser, NSEIndia

try:
    from src.services.nse import get_shareholdings
except ImportError:
    get_shareholdings = None

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


def test_api_client_fetch_xbrl_content(api_client):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"fake content"
    mock_response.headers = {"content-type": "application/xml"}
    mock_session.request.return_value = mock_response
    api_client.session = mock_session

    content = api_client.fetch_xbrl_content("http://fake.url", "TCS")
    assert content == b"fake content"
    mock_session.request.assert_called()


def test_api_client_json_decode_error(api_client):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.json.side_effect = Exception("JSON Decode Error")
    mock_session.request.return_value = mock_response
    api_client.session = mock_session

    result = api_client.integrated_filing_xbrls("TCS")
    assert result == {}


def test_api_client_call_returns_none_when_exhausted(api_client):
    mock_session = MagicMock()
    mock_session.request.side_effect = SessionExhausted("out of retries")
    api_client.session = mock_session

    result = api_client._call("http://fake.url", symbol="TCS")
    assert result is None
    assert mock_session.request.call_count == 1


def test_api_client_call_raises_cookie_error(api_client):
    mock_session = MagicMock()
    mock_session.request.side_effect = CookieError("no cookies")
    api_client.session = mock_session

    with pytest.raises(CookieError):
        api_client._call("http://fake.url", symbol="TCS")


def test_api_client_call_returns_none_on_blocked_response(api_client):
    mock_session = MagicMock()
    mock_session.request.side_effect = BlockedResponse("text/html")
    api_client.session = mock_session

    result = api_client._call("http://fake.url", symbol="TCS")
    assert result is None


def test_set_cookies_delegates_to_prime(api_client):
    mock_session = MagicMock()
    mock_session.prime.return_value = True
    api_client.session = mock_session

    assert api_client._set_cookies("TCS") is True
    mock_session.prime.assert_called_once_with(force=True)


def test_set_cookies_false_when_prime_fails(api_client):
    mock_session = MagicMock()
    mock_session.prime.return_value = False
    api_client.session = mock_session

    assert api_client._set_cookies("TCS") is False


def test_fetch_url_content_returns_none_on_exhaustion(api_client):
    mock_session = MagicMock()
    mock_session.request.side_effect = SessionExhausted("blocked")
    api_client.session = mock_session

    assert api_client.fetch_url_content("http://fake.url") is None


def test_validate_download_rejects_html(api_client):
    resp = MagicMock()
    resp.headers = {"content-type": "text/html"}

    with pytest.raises(BlockedResponse):
        api_client._validate_download(resp)


def test_validate_download_accepts_binary(api_client):
    resp = MagicMock()
    resp.headers = {"content-type": "application/zip"}

    api_client._validate_download(resp)  # should not raise


def test_nse_financials_schema():
    if NSEFinancials is None:
        pytest.skip("NSEFinancials model not available")
    fields = NSEFinancials.model_fields
    assert "financials" in fields
    assert "broadcast_date" in fields
    assert fields["financials"].default_factory is list


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

# NSE shareholding template in use since the Jun-2025 filings: context ids
# carry a "_Context" infix ("..._ContextI") instead of the legacy "...I".
SAMPLE_SHP_XBRL_NEW_FORMAT = b"""<?xml version="1.0" encoding="utf-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-bse-shp="http://www.bseindia.com/xbrl/shp/2025-06-30">
    <in-bse-shp:endDate>2026-06-30</in-bse-shp:endDate>
    <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="ShareholdingOfPromoterAndPromoterGroup_ContextI">0.7177</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
    <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="PublicShareholding_ContextI">0.2823</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
    <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="InstitutionsForeign_ContextI">0.0906</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
    <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="InstitutionsDomestic_ContextI">0.1347</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
    <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="NonInstitutions_ContextI">0.0569</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
    <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="Indian_ContextI">0.7177</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
</xbrli:xbrl>
"""


def test_process_xbrl_filters_quarterly(nse_india):
    """process_xbrl should classify quarterly (OneD/OneI) facts into correct statement docs."""
    mock_record = {"xbrl": MOCK_XBRL_URL, "consolidated": "Consolidated"}
    with unittest.mock.patch.object(
        nse_india.api, "fetch_xbrl_content", return_value=SAMPLE_XBRL
    ):
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
    with unittest.mock.patch.object(
        nse_india.api, "fetch_xbrl_content", return_value=SAMPLE_XBRL
    ):
        result = nse_india.process_xbrl(mock_record, "TEST", "annual-results")
    assert result is not None
    assert result["income_statement"] is not None
    doc = result["income_statement"]
    assert doc["revenue_from_operations"] == "15000000"
    assert doc["profit_loss_for_period"] == "3500000"
    assert doc["diluted_earnings_loss_per_share_from_continuing_operations"] == "2.10"


def test_process_xbrl_keeps_full_year_cash_flow_for_quarterly(nse_india):
    """Q4 integrated filings publish only full-year cash-flow figures; the
    quarterly filter must keep them or cash_flows ends up empty."""
    sample = SAMPLE_XBRL.replace(
        b"</xbrli:xbrl>",
        b'    <in-bse-fin:CashFlowsFromUsedInOperatingActivities contextRef="FourD">4200000'
        b"</in-bse-fin:CashFlowsFromUsedInOperatingActivities>\n"
        b'    <in-bse-fin:CashFlowsFromUsedInOperations contextRef="YearCtx">3900000'
        b"</in-bse-fin:CashFlowsFromUsedInOperations>\n"
        b'    <xbrli:context id="YearCtx">\n'
        b"        <xbrli:entity><xbrli:identifier scheme=\"http://www.bseindia.com\">TEST"
        b"</xbrli:identifier></xbrli:entity>\n"
        b"        <xbrli:period>\n"
        b"            <xbrli:startDate>2025-01-01</xbrli:startDate>\n"
        b"            <xbrli:endDate>2025-12-31</xbrli:endDate>\n"
        b"        </xbrli:period>\n"
        b"    </xbrli:context>\n"
        b"</xbrli:xbrl>",
    )
    mock_record = {"xbrl": MOCK_XBRL_URL, "consolidated": "Consolidated"}
    with unittest.mock.patch.object(
        nse_india.api, "fetch_xbrl_content", return_value=sample
    ):
        result = nse_india.process_xbrl(mock_record, "TEST", "integrated-filing")
    assert result is not None
    assert result["cash_flow"] is not None
    doc = result["cash_flow"]
    assert doc["cash_flows_from_used_in_operating_activities"] == "4200000"
    assert doc["cash_flows_from_used_in_operations"] == "3900000"
    # annual-duration P&L facts must still be excluded from the income statement
    assert doc is not result["income_statement"]
    assert result["income_statement"]["revenue_from_operations"] == "5000000"


def test_process_xbrl_skips_empty_after_filter(nse_india):
    """process_xbrl should return None when no annual-context or annual-duration facts match."""
    sample = (
        SAMPLE_XBRL.replace(b"FourD", b"FiveD")
        .replace(b"2025-01-01", b"2025-07-01")
        .replace(b"2025-12-31", b"2025-09-30")
    )
    mock_record = {"xbrl": MOCK_XBRL_URL, "consolidated": "Consolidated"}
    with unittest.mock.patch.object(
        nse_india.api, "fetch_xbrl_content", return_value=sample
    ):
        result = nse_india.process_xbrl(mock_record, "TEST", "annual-results")
    assert result is None, "Should skip XBRL with no annual facts"


def test_process_xbrl_shareholding_new_context_format(nse_india):
    """NSE's post-Jun-2025 shareholding template uses '..._ContextI' context ids."""
    mock_record = {"xbrl": MOCK_XBRL_URL, "consolidated": "Shareholding"}
    with unittest.mock.patch.object(
        nse_india.api,
        "fetch_xbrl_content",
        return_value=SAMPLE_SHP_XBRL_NEW_FORMAT,
    ):
        result = nse_india.process_xbrl(mock_record, "TEST", "shareholding-pattern")
    assert result is not None
    doc = result["shareholding"]
    assert doc["period_end_date"] == "2026-06-30"
    assert doc["promoters_and_promoter_group"] == "0.7177"
    assert doc["public_shareholding"] == "0.2823"
    assert doc["foreign_institutional_investors"] == "0.0906"
    assert doc["domestic_institutional_investors"] == "0.1347"
    assert doc["non_institutions"] == "0.0569"


def test_nse_financials_fetch():
    nseindia = NSEIndia()
    try:
        # "perform a test fetch in tests/test_nse for SKYGOLD Share"
        integrated = nseindia.api.integrated_filing_xbrls("SKYGOLD")
        integrated_data = (
            integrated.get("data", []) if isinstance(integrated, dict) else []
        )

        if not integrated_data:
            quarterly = nseindia.api.quarterly_results_xbrls("SKYGOLD")
            quarterly_data = (
                quarterly.get("data", quarterly)
                if isinstance(quarterly, dict)
                else quarterly
            )
            if not isinstance(quarterly_data, list):
                quarterly_data = []
            data_list = quarterly_data
            category = "quarterly"
        else:
            data_list = integrated_data
            category = "integrated"
    except CookieError as e:
        pytest.skip(f"NSE session unavailable: {e}")

    found_parsed = False
    for x in data_list:
        ep_key = (
            "integrated-filing" if category == "integrated" else "quarterly-results"
        )
        parsed_data = nseindia.process_xbrl(x, "SKYGOLD", ep_key)
        if parsed_data:
            stmt = (
                parsed_data.get("income_statement")
                or parsed_data.get("balance_sheet")
                or parsed_data.get("cash_flow")
                or parsed_data.get("shareholding")
            )
            assert stmt is not None, "At least one statement doc should be present"
            assert stmt["symbol"] == "SKYGOLD"
            assert "period_end_date" in stmt
            found_parsed = True
            break

    assert found_parsed, "Could not fetch and parse XBRL data for SKYGOLD"


# --- get_shareholdings freshness (DB-first cache) ---


def _shareholding_factory(existing):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute = AsyncMock(return_value=result)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _shareholding_row(pulled_at):
    return SimpleNamespace(
        pulled_at=pulled_at,
        to_dict=lambda: {"symbol": "TEST", "period_end_date": "2026-06-30"},
    )


@pytest.mark.skipif(get_shareholdings is None, reason="services.nse unavailable")
def test_shareholdings_fresh_cache_skips_live_fetch():
    factory = _shareholding_factory(_shareholding_row(datetime.utcnow()))
    with (
        patch("src.services.nse.get_session_factory", return_value=factory),
        patch(
            "src.services.nse.nse_scraper.api.shareholding_xbrls",
            side_effect=AssertionError("live fetch should not happen"),
        ),
    ):
        result = asyncio.run(get_shareholdings("TEST"))

    assert result["shareholdings"]["period_end_date"] == "2026-06-30"


@pytest.mark.skipif(get_shareholdings is None, reason="services.nse unavailable")
def test_shareholdings_stale_cache_triggers_live_fetch():
    factory = _shareholding_factory(
        _shareholding_row(datetime.utcnow() - timedelta(days=8))
    )

    def fake_process(x, symbol, category):
        return {
            "shareholding": {
                "symbol": symbol,
                "period_end_date": "2026-06-30",
                "consolidated": False,
                "source_endpoint": category,
            },
            "income_statement": None,
            "balance_sheet": None,
            "cash_flow": None,
        }

    with (
        patch("src.services.nse.get_session_factory", return_value=factory),
        patch(
            "src.services.nse.nse_scraper.api.shareholding_xbrls",
            return_value=[{"xbrl": "http://x/one.xml", "date": "30-JUN-2026"}],
        ),
        patch(
            "src.services.nse.nse_scraper.process_xbrl",
            side_effect=fake_process,
        ),
    ):
        result = asyncio.run(get_shareholdings("TEST"))

    assert result["shareholdings"]["period_end_date"] == "2026-06-30"

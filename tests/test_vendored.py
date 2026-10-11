from bs4 import BeautifulSoup

from src.services._vendored import (
    _extract_rates_from_tables,
    _tr_to_json,
    nsesymbolpurify,
)


def test_nsesymbolpurify_escapes_ampersand():
    assert nsesymbolpurify("M&M") == "M%26M"
    assert nsesymbolpurify("RELIANCE") == "RELIANCE"


def test_extract_rates_from_tables_maps_keys():
    html = """
    <html><body><table>
      <tr><td>Policy Repo Rate</td><td>6.50 :</td></tr>
      <tr><td>Standing Deposit Facility Rate</td><td>6.25*</td></tr>
      <tr><td>Marginal Standing Facility Rate</td><td>6.75#</td></tr>
    </table></body></html>
    """
    rates = _extract_rates_from_tables(BeautifulSoup(html, "html.parser"))
    assert rates["Policy Repo Rate"] == "6.50"
    assert rates["Savings Deposit Rate"] == "6.25"
    assert rates["Marginal Standing Facility Rate"] == "6.75"


def test_extract_rates_from_tables_parses_tbill_regex():
    html = """
    <html><body>
      <p>The 91 day T-bills auction cut-off yielded 6.12%</p>
      <table><tr><td>Policy Repo Rate</td><td>6.50</td></tr></table>
    </body></html>
    """
    rates = _extract_rates_from_tables(BeautifulSoup(html, "html.parser"))
    assert rates["91 day T-bills"] == "6.12%"


def test_tr_to_json_keeps_stripped_values():
    html = "<div><tr><td>Repo Rate</td><td>6.50 :</td></tr><tr><td>Empty</td><td></td></tr></div>"
    wrapper = BeautifulSoup(html, "html.parser").find("div")
    assert _tr_to_json(wrapper) == {"Repo Rate": "6.50", "Empty": ""}

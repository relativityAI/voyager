from src.exchange.base import StockExchangeBase
from src.utils import console

class NSEIndia(StockExchangeBase):
    def __init__(self):
        super(NSEIndia, self).__init__()

        self.quarterly_context_ref_types=[
            "OneD", 
            "OneI"
            ]
        self.annual_context_ref_types=[
            "FourD"
            ]
        self.shareholding_context_ref_types=[
            'ShareholdingOfPromoterAndPromoterGroupI', 
            "InstitutionsForeignI", 
            "InstitutionsDomesticI", 
            "NonInstitutionsI"
            ]
        self.url_map = {
            "corp_info": "https://www.nseindia.com/api/corp-info?symbol={symbol}&corpType=corpInfo&market=equities",
            "shareholding_pattern": "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={symbol}",
            "announcements_equity": "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}",
            "announcements_sme": "https://www.nseindia.com/api/corporate-announcements?index=sme&symbol={symbol}",
            "annual_reports": "https://www.nseindia.com/api/annual-reports?index=equities&symbol={symbol}",
            "event_calendar": "https://www.nseindia.com/api/event-calendar",
            "quarterly_results": "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={symbol}&period=Quarterly",
            "annual_results": "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={symbol}&period=Annual",
            "integrated_filing": "https://www.nseindia.com/api/integrated-filing-results?&symbol={symbol}",

            "available_equities": "https://www.nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
            "available_sme": "https://www.nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
        }
        

    def _xml_or_html(self, url):
        return url[-3:]

    def _extract_xml_or_html(self, url, symbol):
        try:
            response = self.scraper.api_call(url, symbol=symbol, show_code=False)
            url_type = self._xml_or_html(url)

            if url_type=='xml':
                item = self._process_xml(response,  context_ref_types=self.quarterly_context_ref_types)
            else:
                item = self._process_html(response)

            return item
        except Exception as e:
            console.log(f"[red] \n\nNSEIndia extraction error: \n({url}) \n {e}\n\n")
            raise e

    # Raw XBRLs
    def announcements_xbrls(self, symbol):
        return self.api_call(self.url_map['announcements_equity'].format(symbol=symbol.upper())).json()

    def annual_results_xbrls(self, symbol):
        return self.api_call(self.url_map['integrated_filing'].format(symbol=symbol.upper())).json()

    def quarterly_results_xbrls(self, symbol):
        return self.api_call(self.url_map['quarterly_results'].format(symbol=symbol.upper())).json()

    def integrated_filing_xbrls(self, symbol):
        return self.api_call(self.url_map['integrated_filing'].format(symbol=symbol.upper())).json()

    def shareholding_xbrls(self, symbol):
        return self.api_call(self.url_map['shareholding_pattern'].format(symbol=symbol.upper())).json()

    def annual_reports_xbrls(self, symbol):
        return self.api_call(self.url_map['annual_reports'].format(symbol=symbol.upper())).json()

    

    


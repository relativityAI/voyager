from src.utils.web import generate_fake_headers
import requests
from collections import defaultdict
from pprint import pprint
from bs4 import BeautifulSoup
# from loguru import logger
import json

class Trendlyne(object):
    def __init__(self):
        self.url_format = "https://trendlyne.com/equity/{symbol}/stock-page/"
    
    def _parse_value(self, val):
        if val is None: return None
        if isinstance(val, (int, float)): return val
        s = str(val).replace(",", "").replace("%", "").strip()
        if not s or s == "-": return None
        try:
            if "." in s: return float(s)
            return int(s)
        except:
            return val

    def _flatten_data(self, data):
        flat_data = {}
        
        # SWOT
        swot = data.get('swot', {})
        for label, score_dict in swot.items():
            flat_data[f"SWOT {label} Score"] = self._parse_value(score_dict.get('score'))

        # Technicals
        technicals = data.get('technicals', {})
        parameters = technicals.get('body', {}).get('parameters', {})
        
        for p, p_data in parameters.items():
            if isinstance(p_data, dict):
                if "name" in p_data and "value" in p_data:
                    flat_data[p_data['name']] = self._parse_value(p_data['value'])
                elif "bullish" in p_data and "bearish" in p_data:
                    # MA Signal
                    for k, v in p_data.items():
                        flat_data[f"MA Signal {k}"] = self._parse_value(v)
            elif isinstance(p_data, list):
                for item in p_data:
                    if isinstance(item, dict):
                        name = item.get('name') or item.get('title') or item.get('label')
                        if name:
                            # Use key as prefix if it's EMA/SMA etc.
                            prefix = p.replace("_parameters", "").replace("_analysis", "").replace("_insight", "").upper()
                            key = f"{prefix} {name}"
                            if "value" in item: flat_data[key] = self._parse_value(item['value'])
                            elif "data" in item: flat_data[key] = self._parse_value(item['data'])
                            
                            if "changePercent" in item: flat_data[f"{key} Change %"] = self._parse_value(item['changePercent'])
                            if "changePercentSafe" in item: flat_data[f"{key} Change %"] = self._parse_value(item['changePercentSafe'])
            else:
                flat_data[p] = self._parse_value(p_data)

        return flat_data

    def fetch(self, symbol = "KEI"):
        url = self.url_format.format(symbol=symbol)
        # logger.info(f"Endpoint : {url}")
        response = requests.get(url, headers=generate_fake_headers(), allow_redirects=True)
        assert response.status_code == 200

        encoded_url = response.url.split('/')

        code = encoded_url[-4]
        symbol = encoded_url[-3]
        slug = encoded_url[-2]


        soup = BeautifulSoup(response.content, 'html.parser')
        data = defaultdict(dict)

        metrics_api_url = f"https://trendlyne.com/equity/getStockMetricParameterList/{code}/"

        swot = soup.find('div', class_= "swot-main-holder").find_all('a')
        data['swot'] = defaultdict(dict)
        for i in swot:
            label = i.find('div').get_text().strip()
            data['swot'][label]['score'] = float(i.find('p').get_text().strip())
        # href = swot[0]['href']
        # r = requests.get(href, headers=generate_fake_headers())
        # soup = BeautifulSoup(r.content, 'html.parser')

        # strengths = [x.get_text().strip() for x in soup.find('div', id='tag_strengths').find_all('a') ]
        # weakness = [x.get_text().strip() for x in soup.find('div', id='tag_weakness').find_all('a')]
        # opportunity = [x.get_text().strip() for x in soup.find('div', id='tag_opportunity').find_all('a')]
        # threats = [x.get_text().strip() for x in soup.find('div', id='tag_threats').find_all('a')]
        # others = [x.get_text().strip() for x in soup.find('div', id='tag_others').find_all('a')]

        # data['swot']['S']['tags'] = strengths
        # data['swot']['W']['tags'] = weakness
        # data['swot']['O']['tags'] = opportunity
        # data['swot']['T']['tags'] = threats
        # data['swot']['Other']['tags'] = strengths
        # data['swot']['Other']['score'] = len(others)


        # trendlyne_checklist = soup.find('div', id="trendlyne_checklist")
        # data['trendlyne_checklist'] = json.loads(trendlyne_checklist['data-checklistdict'])


        # key_metrics = soup.find('div', id="stock_key_metrics" )
        # data['key_metrics'] = json.loads(key_metrics['data-metrics'])


        # performance = soup.find('div', id="stock_performance_parameters")
        # data['stock_performance_parameters'] = json.loads(performance['data-metrics'])


        # recommendation = soup.find('section', id= "consensus-results")
        # data['recommendation'] = json.loads(recommendation['data-consensusjson'])


        technicals_url = f"https://trendlyne.com/equity/technical-analysis/{symbol}/{code}/{slug}/"
        technicals_api_url = f"https://trendlyne.com/equity/api/stock/adv-technical-analysis/{code}/24/?format=json"
        r = requests.get(technicals_api_url, headers=generate_fake_headers()).json()
        data['technicals'] = r
        

        # shareholdings = soup.find('div', id= "shareholding-tables")
        # all_shareholdings_url = shareholdings.find('a', class_="lazyload-btn") # all technicals - fetch all


        # deals = soup.find('div', id= "deals")
        # all_deals_url = deals.find('a', class_="lazyload-btn") # all technicals - fetch all
        

        return self._flatten_data(data)

        
    def format_output(self, data:dict):

        output = """"""        

        swot = data['swot']
        for s in swot.keys():
            output+=f"{s}: "
            output+=str(swot[s]['score'])
            output+="\n"

        output+="\n"

        parameters = data['technicals']['body']['parameters']
        
        for p in parameters.keys():
            if type(parameters[p]) == dict:
                if "name" in parameters[p].keys() and "value" in parameters[p].keys():
                    name = parameters[p]['name']
                    val = parameters[p]['value']

                    output+= f"{name}: "
                    output+= str(val)
                    output+= "\n"



            elif type(parameters[p]) == list:
                for i in parameters[p]:
                    if type(i) == dict:
                        if "name" in i.keys() :
                            output += f"{p} {i['name']}: "
                            if "value" in i.keys(): output+= str(i['value'])
                            if "changePercentSafe" in i.keys(): output+= str(i['changePercentSafe'])
                            output+= "\n"
        
                    else:
                        output+= f"{i}: "
                        output+= parameters[p][i]
                        output+= "\n"

                output+= "\n"


            else:
                output+= f"{p}: "
                output+= str(parameters[p])
                output+= "\n"

            

        return output.strip()





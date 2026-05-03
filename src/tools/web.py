from datetime import datetime
from pprint import pprint
from io import BytesIO
import requests
import re

# from duckduckgo_search import DDGS
# from serpapi import GoogleSearch
#  pip install google-search-results

from bs4 import BeautifulSoup
from pypdf import PdfReader
import os
from dotenv import load_dotenv
load_dotenv()




class WebHelper:
    def __init__(self):
        self.current_year = datetime.now().year

        # self.ddgs = DDGS(
        #     headers={
        #         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"
        #     }
        # )
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.session = requests.Session()# Create a session to persist cookies
        self.session.headers = self.headers
        self.num_results = 3
        self.serp_key = os.getenv('SERP_KEY')

    # def web_search(
    #         self,
    #         search_query
    # ):
    #     results = self.ddgs.text(
    #         keywords = search_query,
    #         region="in-en",
    #         max_results=self.num_results
    #     )
    #     unique_urls = list(set(item['href'] for item in results))
    #     return unique_urls

    def serp_web_search(self, search_query, time_range="", domains=None, num_results=5):
        time_filters = {
            "week": "qdr:w",
            "month": "qdr:m",
            "year": "qdr:y"
        }
        
        # Format domain filtering
        if domains:
            pprint("DOMAINS")
            pprint(domains)
            domain_str_list = [f"site:{domain}" for domain in domains]
            domain_filter = " OR ".join(domain_str_list)
            search_query = f"({search_query}) ({domain_filter})"

        params = {
            "engine": "google",
            "q": search_query,
            "api_key": self.serp_key,
            "num": num_results,
        }
        
        if time_range in time_filters:
            params["tbs"] = time_filters[time_range]

        search = GoogleSearch(params)
        results = search.get_dict()

        organic_results = results.get("organic_results", [])
        filtered_results = [result['link'] for result in organic_results]

        print("---------------------------")
        print("Filtered URL Results")
        pprint(filtered_results)
        return filtered_results

    def doc_search(
            self,
            search_query
            ):
        search_query += " filetype:pdf"
        return self.web_search(search_query=search_query)

    def exchange_search(
            self,
            search_query
            ):
        search_query += " filetype:pdf"
        search_query += " site:bseindia.com"
        return self.web_search(search_query=search_query)

    def convert_key_format(self, key: str) -> str:
        key = re.sub(r"[/'\"]", "", key)  # Remove slashes and quotes
        return key.lower()

    def convert_pdf_date(self, pdf_date: str) -> str:
        match = re.search(r"D:(\d{4})(\d{2})(\d{2})", pdf_date)
        if not match:
            raise ValueError("Invalid date format")
        
        year, month, day = match.groups()
        formatted_date = f"{year}-{month}-{day}"
        return formatted_date

    def format_metadata(self, pdf_metadata: dict) -> dict:
        formatted_dict = {}
        for key, value in pdf_metadata.items():
            new_key = self.convert_key_format(key)
            if isinstance(value, str) and value.startswith("D:"):
                value = self.convert_pdf_date(value)
            formatted_dict[new_key] = value
        return formatted_dict

    def load_pdf(
            self,
            response
    ):
        pdf_bytes = BytesIO(response.content)
        pdf_reader = PdfReader(pdf_bytes)
        # metadata_main = self.format_dict(pdf_reader.metadata)
        return pdf_reader
        
    def load_website(
            self,
            response
    ):
        soup = BeautifulSoup(response.content, 'html.parser')
        return soup.text

    def load_url_text(
            self,
            url
    ):
        try:
            response = self.session.get(url, timeout=10) # get the necessary cookies
        except:
            print(f"URL timed out : {url}")
            return None

        content_type = response.headers.get('content-type')
        if 'application/pdf' in content_type:
            pdf_reader = self.load_pdf(response=response)
            metadata = self.format_metadata(pdf_reader.metadata)
            text = """"""
            if 'creationdate' in metadata.keys():
                text += f"Date : {metadata['creationdate']}"
            elif 'moddate' in metadata.keys():
                text += f"Date : {metadata['moddate']}"
            
            for page in pdf_reader.pages:
                text += "\n"
                text += page.extract_text()

            return text

        elif 'text/html' in content_type:
            text = self.load_website(response=response)
            return text
        else:
            print('Unknown type: {}'.format(content_type))
            return None

class TavilyWebHelper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.session = requests.Session()  # Create a session to persist cookies
        self.session.headers = self.headers
        self.num_results = 5
        self.tavily_search_url = "https://api.tavily.com/search"
        self.tavily_payload = {
            "query": None,
            "topic": "general",
            "search_depth": "advanced",
            "max_results": self.num_results,
            "time_range": None,
            "days": 7,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_image_descriptions": False,
            "include_domains": [],
            "exclude_domains": []
        }
        self.tavily_headers = {
            "Authorization": "Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def tavily_call(
            self,
            query,
            api_key,
            search_depth,
            time_range : str,
            include_domains: list,
            max_results = 10
        ):
        """ THe amazing thing is that the results are sorted by higher relevance - so might have to change that later in a self implementation """
        payload = self.tavily_payload
        payload['query'] = query
        payload['time_range'] = time_range
        payload['include_domains'] = include_domains
        payload['max_results'] = max_results
        payload['search_depth'] = search_depth # basic, advanced

        headers = self.tavily_headers
        headers['Authorization'] = headers['Authorization'].format(api_key=api_key)
        response = requests.request(
            "POST", 
            self.tavily_search_url, 
            json=payload, 
            headers=headers
            )
        
        # print(response.text)

        return response.json()

    def web_search(
        self, 
        api_key,
        query,
        search_depth,
        time_range,
        include_domains,
        max_results
        ):
        # results = self.tavily_client.search(query=search_query, search_depth="basic", max_results=self.num_results)
        results = self.tavily_call(
            query=query,
            api_key=api_key,
            search_depth=search_depth,
            time_range =time_range,
            include_domains = include_domains,
            max_results = max_results
        )

        unique_urls = list(set(item['url'] for item in results["results"]))
        return unique_urls
    
    def convert_key_format(self, key: str) -> str:
        key = re.sub(r"[/'\"]", "", key)  # Remove slashes and quotes
        return key.lower()
    
    def convert_pdf_date(self, pdf_date: str) -> str:
        match = re.search(r"D:(\d{4})(\d{2})(\d{2})", pdf_date)
        if not match:
            raise ValueError("Invalid date format")
        
        year, month, day = match.groups()
        formatted_date = f"{year}-{month}-{day}"
        return formatted_date
    
    def format_metadata(self, pdf_metadata: dict) -> dict:
        formatted_dict = {}
        for key, value in pdf_metadata.items():
            new_key = self.convert_key_format(key)
            if isinstance(value, str) and value.startswith("D:"):
                value = self.convert_pdf_date(value)
            formatted_dict[new_key] = value
        return formatted_dict
    
    def load_pdf(self, response):
        pdf_bytes = BytesIO(response.content)
        pdf_reader = PdfReader(pdf_bytes)
        return pdf_reader
    
    def load_website(self, response):
        soup = BeautifulSoup(response.content, 'html.parser')
        return soup.text
    
    def load_url_text(self, url):
        try:
            response = self.session.get(url, timeout=10)  # Get the necessary cookies
        except:
            print(f"URL timed out: {url}")
            return None

        content_type = response.headers.get('content-type')
        if 'application/pdf' in content_type:
            pdf_reader = self.load_pdf(response=response)
            metadata = self.format_metadata(pdf_reader.metadata)
            text = """"""
            if 'creationdate' in metadata.keys():
                text += f"Date: {metadata['creationdate']}"
            elif 'moddate' in metadata.keys():
                text += f"Date: {metadata['moddate']}"
            
            for page in pdf_reader.pages:
                text += "\n"
                text += page.extract_text()
            return text
        
        elif 'text/html' in content_type:
            return self.load_website(response=response)
        else:
            print(f'Unknown type: {content_type}')
            return None

if __name__=="__main__":
    from pprint import pprint
    web_helper = WebHelper()
    results = web_helper.web_search("Solar Industries Ltd")
    pprint(results)
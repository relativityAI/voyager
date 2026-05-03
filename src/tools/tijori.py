from urllib.parse import quote
from src.utils.web import generate_fake_headers
import requests
from bs4 import BeautifulSoup

class Tijori(object):
    def __init__(self):
        self.search = "https://www.tijorifinance.com/api/v1/ind/company_search/?q={share_name}"
        self.company = "https://www.tijorifinance.com/company/"

    def fetch(self, share_name):
        search_url = self.search.format(share_name=quote(share_name))
        response = requests.get(search_url, headers=generate_fake_headers()).json()
        slug = response[0]['slug']

        company_url = self.company + slug
        response = requests.get(company_url, headers=generate_fake_headers())

        if response.status_code != 200:
            raise Exception(f"Could not fetch : {company_url}")

        soup = BeautifulSoup(response.content, 'html.parser')

        custom_ratios = soup.find_all('div', class_ = "custom_ratio")
        about = soup.find('div', class_ = "about")
        forensic = soup.find('section', id = "forensic")
        marketshare = soup.find('section', id = "marketshare")
        revenuemix = soup.find('section', id = "revenuemix")
        competitors = soup.find('section', id = "competitors")
        connections = soup.find('section', id = "connections") # customers
        discussions_and_analysis = soup.find('section', id = "discussions_and_analysis") 


        print(revenuemix)




if __name__ == "__main__":
    tijori = Tijori()
    tijori.fetch("kei industries")

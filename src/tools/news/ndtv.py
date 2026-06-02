# News
# https://archives.ndtv.com/

from src.utils.web import generate_fake_headers
from src.utils.rate_limiter import get_rate_limiter
from bs4 import BeautifulSoup
import requests
from pprint import pprint
import sys
from datetime import datetime

# Rate limiter for NDTV API
_ndtv_rate_limiter = get_rate_limiter("ndtv", calls_per_second=10)

def generate_archive_url( year = 2025, month = 11 ):
    return f"https://archives.ndtv.com/articles/{year}-{month}.html"


def scrape_news(
    from_year:int = 2025,
    from_mon:int = 10,
    to_year:int = 2025,
    to_mon:int = 11,
    allowed_categories=[
        "business-news", 
        "world-news",
        "india-news"
        ]
):

    items = []

    url = generate_archive_url()
    headers = generate_fake_headers()
    
    # Apply rate limiting before making the request
    _ndtv_rate_limiter.wait()
    response = requests.get(url, headers = headers, timeout=10)

    status_code = response.status_code

    if status_code != 200:
        return 

    soup = BeautifulSoup(response.content, 'html.parser')

    main_content = soup.find('div', id = "main-content")

    lists = main_content.find_all('ul')
    dates = main_content.find_all('h3')

    if not len(lists) == len(dates):
        print("Something wrong with the sizees")
        return


    if lists:
        for index, l in enumerate(lists):

            date = dates[index].get_text()
            date = datetime.strptime(date, "%d %B %Y").strftime("%Y-%m-%d")

            links = l.find_all('a', href=True)
            for idx, a in enumerate(links):
                title = a.get_text()
                url = a['href']

                category = None
                if "https://www.ndtv.com/" in url:
                    category = url.split("/")[3]

                if category in allowed_categories:
                    items.append(
                        {
                            "source" : "ndtv",
                            "category" : category,
                            "title" : title,
                            "url" : url,
                            "date" : date
                        }
                    )

                    # print(f"{category} - {a.get_text()}")

    return items

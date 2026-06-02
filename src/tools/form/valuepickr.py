# Form
# https://forum.valuepickr.com/search?q=genus%20power%20order%3Alatest
# https://forum.valuepickr.com/search?q=genus+power


import requests
from bs4 import BeautifulSoup
from requests.utils import requote_uri

from src.utils.rate_limiter import get_rate_limiter
from src.utils.web import generate_fake_headers

# Rate limiter for ValuePickr API
_valuepickr_rate_limiter = get_rate_limiter("valuepickr", calls_per_second=10)


def search_forum(query: str):
    url = requote_uri(f"https://forum.valuepickr.com/search.json?q={query}")

    # Apply rate limiting before making the request
    _valuepickr_rate_limiter.wait()
    response = requests.get(url, headers=generate_fake_headers(), timeout=10).json()

    # pprint(response['grouped_search_result'])
    topics = response["topics"]
    # pprint(response['posts'])

    return topics

    # df = pd.DataFrame(topics)
    # print(df.iloc[0]['slug'])

    # for t in topics:
    #     print(t["title"], t["fancy_title"], t["posts_count"], t["id"], t["slug"])

    # topic_id = topics[0]["id"]
    # slug = topics[0]["slug"]

    # topic_url = f"https://forum.valuepickr.com/t/{slug}/{topic_id}.json"
    # topic_data = requests.get(topic_url).json()

    # for post in topic_data["post_stream"]["posts"]:
    #     from bs4 import BeautifulSoup
    #     text = BeautifulSoup(post["cooked"], "html.parser").get_text(separator="\n", strip=True)
    #     print(text)


def scrape_thread():
    """
    Discourse loads posts dynamically using JavaScript.
    Requests does not execute JavaScript, so the HTML returned is often just the shell, not the actual post text.
    If div.cooked is missing, that means you hit the JS-only shell page.
    Solution: Use the Discourse API endpoint instead
    Every Discourse post has a JSON API version.
    For example:
    https://forum.valuepickr.com/t/{slug}/{topic_id}.json

    For your example:
    https://forum.valuepickr.com/t/action-construction-equipment-ltd/397.json
    Get JSON directly:
    import requests
    url = "https://forum.valuepickr.com/t/action-construction-equipment-ltd/397.json"
    """

    # url = "https://forum.valuepickr.com/t/action-construction-equipment-ltd/397/44"

    url = "https://forum.valuepickr.com/t/action-construction-equipment-ltd/397.json"

    # Apply rate limiting before making the request
    _valuepickr_rate_limiter.wait()
    data = requests.get(url, headers=generate_fake_headers(), timeout=10).json()

    soup = BeautifulSoup(data["post_stream"]["posts"][0]["cooked"], "html.parser")

    print(soup.get_text(separator="\n", strip=True))

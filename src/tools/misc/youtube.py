# Misc
# https://www.youtube.com/results?search_query=scrape+youtube

# from src.utils.web import generate_fake_headers
# from bs4 import BeautifulSoup
# import requests

from youtube_search import YoutubeSearch
from youtube_transcript_api import YouTubeTranscriptApi, FetchedTranscript
from pprint import pprint


def generate_yt_search_url(query:str):
    return f"https://www.youtube.com/results?search_query={'+'.join(query.split())}"

def youtube_search_results(query, max_results=10):
    results = YoutubeSearch(query, max_results=max_results).to_dict()
    return results

def fetch_transcripts(id_or_url: str):
    vid = None
    if "www.youtube.com/watch?v=" in id_or_url:
        vid = id_or_url.split("=")[-1]
    else:
        vid = id_or_url


    ytt_api = YouTubeTranscriptApi()
    fetched_transcript = ytt_api.fetch(vid, languages=['de', 'hi'])

    return fetched_transcript

def parse_transcripts(tr: FetchedTranscript):
    full = []
    for snippet in tr:
        full.append(snippet.text)
    return "\n".join(full)
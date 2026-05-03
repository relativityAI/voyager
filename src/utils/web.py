from fake_useragent import UserAgent
import logging


def generate_fake_user_agent():
    try:
        return UserAgent().random
    except Exception as e:
        logging.error(f"Failed to get fake user agent")
        logging.error(f"{e}")
        return None

def generate_fake_headers():
    f_u_a = generate_fake_user_agent()
    if not f_u_a:
        f_u_a = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    return {
        "User-Agent" : f_u_a,
        "Accept": "application/json, text/plain, */*",
        "Connection": "keep-alive",
    }

"""Reddit mention search via PRAW (official API)."""

import os
from typing import Any, Dict, List

DEFAULT_SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "investing",
    "IndiaInvestments",
    "IndianStockMarket",
]


def _client():
    from src.services._common import ServiceUnavailableError  # deferred: breaks cycle

    try:
        import praw
    except ImportError as exc:
        raise ServiceUnavailableError(
            "praw is not installed. Run: pip install praw"
        ) from exc
    creds = {
        k: os.getenv(k) for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
    }
    if not all(creds.values()):
        raise ServiceUnavailableError(
            "Reddit API credentials missing. Set REDDIT_CLIENT_ID, "
            "REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT."
        )
    return praw.Reddit(client_id=creds["REDDIT_CLIENT_ID"],
                       client_secret=creds["REDDIT_CLIENT_SECRET"],
                       user_agent=creds["REDDIT_USER_AGENT"])


def reddit_search(
    query: str,
    subreddits: List[str],
    limit: int = 10,
) -> List[Dict[str, Any]]:
    client = _client()
    posts = []
    for sub in subreddits:
        try:
            subreddit = client.subreddit(sub)
            for submission in subreddit.search(query, sort="new", time_filter="month", limit=limit):
                posts.append(
                    {
                        "id": submission.id,
                        "title": submission.title,
                        "subreddit": sub,
                        "score": submission.score,
                        "num_comments": submission.num_comments,
                        "created_utc": submission.created_utc,
                        "url": submission.url,
                        "permalink": f"https://www.reddit.com{submission.permalink}",
                    }
                )
        except Exception:
            # A single subreddit failing shouldn't kill the whole query.
            continue
    posts.sort(key=lambda p: p.get("score") or 0, reverse=True)
    return posts[:limit]


async def search_reddit(query: str, limit: int = 10) -> Dict[str, Any]:
    import asyncio

    from src.services._common import ServiceUnavailableError, UpstreamError
    from src.utils.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter("reddit", calls_per_second=1.0)
    limiter.wait()

    subreddits = DEFAULT_SUBREDDITS
    try:
        posts = await asyncio.to_thread(reddit_search, query, subreddits, limit)
        return {"query": query, "source": "reddit", "count": len(posts), "posts": posts}
    except (ServiceUnavailableError, UpstreamError):
        raise
    except Exception as exc:
        raise UpstreamError(f"Reddit search failed: {exc}")

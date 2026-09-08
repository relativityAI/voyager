"""YouTube search + transcript retrieval.

Search runs through ``yt-search-python`` (innertube endpoints, no API key).
Transcripts come from ``youtube-transcript-api`` (captions, no headless
browser). Both feeds the management/sentiment pipeline.
"""

from typing import Any, Dict, List


def _err(name: str):
    from src.services._common import (  # deferred: breaks services import cycle
        NotFoundError,
        ServiceUnavailableError,
        UpstreamError,
    )
    return {"NotFoundError": NotFoundError, "ServiceUnavailableError": ServiceUnavailableError,
            "UpstreamError": UpstreamError}[name]


def youtube_search(
    query: str, limit: int, region: str = "US"
) -> List[Dict[str, Any]]:
    try:
        from youtubesearchpython import VideosSearch
    except ImportError as exc:
        raise _err("ServiceUnavailableError")(
            "yt-search-python is not installed. Run: pip install yt-search-python"
        ) from exc
    try:
        search = VideosSearch(query, limit=limit, region=region)
        results = search.result().get("result")
    except Exception as exc:
        raise _err("UpstreamError")(f"YouTube search failed: {exc}") from exc
    videos = []
    for r in results or []:
        videos.append(
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "channel": (r.get("channel") or {}).get("name"),
                "views": r.get("viewCount"),
                "published": r.get("publishedTime"),
                "duration": r.get("duration"),
                "url": r.get("link"),
            }
        )
    return videos


def get_transcript(video_id: str) -> List[Dict[str, Any]]:
    try:
        from youtube_transcript_api import (
            NoTranscriptFound,
            TranscriptsDisabled,
            YouTubeTranscriptApi,
        )
    except ImportError as exc:
        raise _err("ServiceUnavailableError")(
            "youtube-transcript-api is not installed. Run: pip install youtube-transcript-api"
        ) from exc
    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id).to_raw_data()
    except TranscriptsDisabled as exc:
        raise _err("NotFoundError")("Captions are disabled for this video") from exc
    except NoTranscriptFound as exc:
        raise _err("NotFoundError")("No transcript available for this video") from exc
    except Exception as exc:
        raise _err("UpstreamError")(f"Failed to fetch transcript: {exc}") from exc
    return [
        {"text": t.get("text"), "start": t.get("start"), "duration": t.get("duration")}
        for t in transcript
    ]


def transcript_to_text(transcript: List[Dict[str, Any]]) -> str:
    return " ".join(t.get("text", "") for t in transcript)

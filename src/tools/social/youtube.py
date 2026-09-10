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

    return {
        "NotFoundError": NotFoundError,
        "ServiceUnavailableError": ServiceUnavailableError,
        "UpstreamError": UpstreamError,
    }[name]


def youtube_search(query: str, limit: int, region: str = "US") -> List[Dict[str, Any]]:
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
        # YouTube bot-checks the plain client from cloud IPs (Render). Fall back
        # to yt-dlp with TLS impersonation, which presents as a real browser.
        transcript = _ytdlp_transcript(video_id, exc)
    return [
        {"text": t.get("text"), "start": t.get("start"), "duration": t.get("duration")}
        for t in transcript
    ]


def _ytdlp_transcript(video_id: str, cause: BaseException) -> List[Dict[str, Any]]:
    """Fetch a transcript via yt-dlp + curl_cffi TLS impersonation.

    youtube-transcript-api's plain client gets bot-checked from cloud IPs;
    yt-dlp extracts a signed caption URL, which we fetch with a browser TLS
    fingerprint (curl_cffi's ``impersonate``).
    """
    try:
        from yt_dlp import YoutubeDL
        from curl_cffi import requests as creq
    except ImportError as exc:
        raise _err("ServiceUnavailableError")(
            "yt-dlp/curl-cffi not installed. Run: pip install yt-dlp curl-cffi"
        ) from exc
    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}")
        tracks = []
        for group in (
            info.get("automatic_captions") or {},
            info.get("subtitles") or {},
        ):
            for lang, items in group.items():
                tracks.extend({"lang": lang, **t} for t in items)
        tracks = [t for t in tracks if t.get("ext") == "json3" and t.get("url")]
        en_rank = {
            lang: i
            for i, lang in enumerate(["a.en", "en", "en-orig", "en-US", "en-GB"])
        }
        tracks.sort(
            key=lambda t: (
                en_rank.get(t["lang"], 99),
                0 if "a." in t["lang"] or t["lang"] in en_rank else 1,
            )
        )
        track = tracks[0] if tracks else None
        if track:
            resp = creq.get(track["url"], impersonate="chrome", timeout=30)
            resp.raise_for_status()
            return _parse_caption_json(resp.json())
    except Exception as exc:  # noqa: BLE001
        raise _err("UpstreamError")(
            f"Failed to fetch transcript: {cause}; yt-dlp fallback failed too: {exc}"
        ) from exc
    raise _err("NotFoundError")("No transcript available for this video")


def _parse_caption_json(data: dict) -> List[Dict[str, Any]]:
    events = data.get("events") or []
    return [
        {
            "text": " ".join(
                seg.get("utf8", "").strip()
                for seg in evt.get("segs", [])
                if seg.get("utf8")
            ),
            "start": evt.get("tStartMs", 0) / 1000.0,
            "duration": evt.get("dDurationMs", 0) / 1000.0,
        }
        for evt in events
        if evt.get("segs")
    ]


def transcript_to_text(transcript: List[Dict[str, Any]]) -> str:
    return " ".join(t.get("text", "") for t in transcript)

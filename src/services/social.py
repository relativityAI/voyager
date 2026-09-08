"""Social/media service: Reddit mentions and YouTube search/transcripts."""

import asyncio
from typing import Any, Dict

from ..tools.social.reddit import search_reddit
from ..tools.social.youtube import get_transcript, transcript_to_text, youtube_search
from ._common import InvalidRequestError, UpstreamError


async def get_reddit(query: str, limit: int = 10) -> Dict[str, Any]:
    if not query.strip():
        raise InvalidRequestError("query is required")
    if limit < 1 or limit > 50:
        raise InvalidRequestError("limit must be between 1 and 50")
    return await search_reddit(query, limit)


async def get_youtube_search(query: str, limit: int = 15) -> Dict[str, Any]:
    if not query.strip():
        raise InvalidRequestError("query is required")
    if limit < 1 or limit > 50:
        raise InvalidRequestError("limit must be between 1 and 50")
    try:
        videos = await asyncio.to_thread(youtube_search, query, limit)
        return {"query": query, "source": "youtube", "count": len(videos), "videos": videos}
    except UpstreamError:
        raise
    except Exception as exc:
        raise UpstreamError(f"YouTube search failed: {exc}")


async def get_youtube_transcript(video_id: str) -> Dict[str, Any]:
    if not video_id:
        raise InvalidRequestError("video_id is required")
    transcript = await asyncio.to_thread(get_transcript, video_id)
    return {
        "video_id": video_id,
        "text": transcript_to_text(transcript),
        "segments": transcript,
    }

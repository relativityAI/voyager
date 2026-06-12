"""
youtube_parser.py — Extract text from a YouTube video.

Strategy (tried in order):
  1. Fetch official / auto-generated captions via ``youtube-transcript-api``
     (fast, no download, no ffmpeg required).
  2. If no transcript is available, download the best audio track with
     ``yt-dlp`` and transcribe it locally with OpenAI Whisper.

Requires:
    pip install youtube-transcript-api yt-dlp openai-whisper
    # System dependency for audio fallback: ffmpeg
    #   Ubuntu/Debian: sudo apt install ffmpeg
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from loguru import logger

from src.utils.fileparsers.base import BaseParser, ParseResult

# Regex patterns that identify a YouTube URL
_YT_URL_PATTERNS = [
    re.compile(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})"),
]


def _extract_video_id(url: str) -> str | None:
    """Return the 11-character YouTube video ID from *url*, or ``None``."""
    for pat in _YT_URL_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


class YouTubeParser(BaseParser):
    """Fetch text from a YouTube video — transcript first, audio fallback.

    Args:
        preferred_languages: Ordered list of language codes to try when
                             fetching official transcripts.
                             Default: ``["en"]``.
        whisper_model:       Whisper model to use for the audio fallback.
                             Options: ``"tiny"``, ``"base"``, ``"small"``,
                             ``"medium"``, ``"large"``.
                             Default: ``"base"``.
        whisper_language:    Language hint passed to Whisper. ``None`` →
                             auto-detect.
        audio_format:        yt-dlp audio format selector.
                             Default: ``"bestaudio/best"``.
        keep_audio:          Keep the downloaded audio file after transcription.
                             Default: ``False``.

    Example::

        parser = YouTubeParser(preferred_languages=["en", "de"])
        result = parser.parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        print(result.text)
        print(result.metadata["method"])  # "transcript" or "whisper"
    """

    label = "YouTubeParser"

    def __init__(
        self,
        preferred_languages: list[str] | None = None,
        whisper_model: str = "base",
        whisper_language: str | None = None,
        audio_format: str = "bestaudio/best",
        keep_audio: bool = False,
    ) -> None:
        self.preferred_languages = preferred_languages or ["en"]
        self.whisper_model = whisper_model
        self.whisper_language = whisper_language
        self.audio_format = audio_format
        self.keep_audio = keep_audio

    # ------------------------------------------------------------------
    def parse(
        self,
        source: str,
        *,
        extract_images: bool = False,  # thumbnails not yet implemented
    ) -> ParseResult:
        """Extract text from a YouTube URL.

        Args:
            source:         Full YouTube URL (watch page or youtu.be short link).
            extract_images: Not currently implemented; silently ignored.

        Returns:
            :class:`~src.utils.fileparsers.base.ParseResult` with ``text``
            and ``metadata["method"]`` indicating whether a transcript or
            Whisper was used.

        Raises:
            ValueError: If *source* is not a recognisable YouTube URL.
        """
        video_id = _extract_video_id(source)
        if video_id is None:
            raise ValueError(
                f"Cannot extract a YouTube video ID from: {source!r}\n"
                "Make sure the URL contains a valid video ID."
            )

        logger.info(f"[YouTubeParser] Video ID: {video_id}")

        # --- Strategy 1: transcript API ---
        result = self._try_transcript(video_id, source)
        if result is not None:
            return result

        # --- Strategy 2: download audio + Whisper ---
        logger.info(
            "[YouTubeParser] No transcript available — "
            "falling back to audio download + Whisper transcription."
        )
        return self._transcribe_audio(video_id, source)

    # ------------------------------------------------------------------
    # Strategy 1 — Transcript API
    # ------------------------------------------------------------------

    def _try_transcript(self, video_id: str, source: str) -> ParseResult | None:
        """Return a ParseResult from official captions, or ``None`` on failure."""
        try:
            from youtube_transcript_api import (  # noqa: PLC0415
                NoTranscriptFound,
                TranscriptsDisabled,
                YouTubeTranscriptApi,
            )
        except ImportError:
            logger.warning(
                "[YouTubeParser] youtube-transcript-api not installed; "
                "skipping transcript strategy. pip install youtube-transcript-api"
            )
            return None

        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            # Try preferred languages first, then any available transcript
            transcript = None
            try:
                transcript = transcript_list.find_transcript(self.preferred_languages)
            except NoTranscriptFound:
                # Fall back to auto-generated in preferred langs, then any lang
                try:
                    transcript = transcript_list.find_generated_transcript(
                        self.preferred_languages
                    )
                except NoTranscriptFound:
                    # Last resort: first available
                    available = list(transcript_list)
                    if available:
                        transcript = available[0]

            if transcript is None:
                logger.info("[YouTubeParser] No transcript found via API.")
                return None

            entries = transcript.fetch()
            # Each entry: {"text": ..., "start": ..., "duration": ...}
            full_text = " ".join(e["text"] for e in entries).strip()
            total_duration = (
                entries[-1]["start"] + entries[-1]["duration"] if entries else 0.0
            )
            lang = getattr(transcript, "language_code", "unknown")

            logger.info(
                f"[YouTubeParser] Transcript fetched via API. "
                f"language='{lang}', chars={len(full_text)}"
            )

            return ParseResult(
                text=full_text,
                metadata={
                    "method": "transcript",
                    "language": lang,
                    "is_generated": getattr(transcript, "is_generated", None),
                    "duration_seconds": round(total_duration, 2),
                    "video_id": video_id,
                },
                source=source,
            )

        except TranscriptsDisabled:
            logger.info(
                f"[YouTubeParser] Transcripts are disabled for video {video_id}."
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[YouTubeParser] Transcript API error: {exc}")
            return None

    # ------------------------------------------------------------------
    # Strategy 2 — yt-dlp download + Whisper
    # ------------------------------------------------------------------

    def _transcribe_audio(self, video_id: str, source: str) -> ParseResult:
        """Download audio with yt-dlp, then transcribe with Whisper."""
        # --- Verify yt-dlp ---
        try:
            import yt_dlp  # noqa: PLC0415, F401
        except ImportError as exc:
            raise ImportError(
                "yt-dlp is required for the audio fallback: pip install yt-dlp"
            ) from exc

        # --- Verify Whisper ---
        try:
            import whisper  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "openai-whisper is required for the audio fallback: "
                "pip install openai-whisper\n"
                "Also ensure ffmpeg is installed: sudo apt install ffmpeg"
            ) from exc

        tmp_dir = Path(tempfile.mkdtemp(prefix="voyager_yt_"))
        audio_path: Path | None = None

        try:
            # --- Download audio ---
            output_template = str(tmp_dir / "%(id)s.%(ext)s")
            ydl_opts = {
                "format": self.audio_format,
                "outtmpl": output_template,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "128",
                    }
                ],
                "quiet": True,
                "no_warnings": True,
            }

            logger.info(f"[YouTubeParser] Downloading audio from {source} …")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(source, download=True)
                video_title = info.get("title", video_id)
                video_duration = info.get("duration", 0)

            # Locate the downloaded file
            mp3_files = list(tmp_dir.glob("*.mp3"))
            if not mp3_files:
                all_audio = list(tmp_dir.glob("*"))
                if not all_audio:
                    raise RuntimeError(
                        "yt-dlp did not produce any audio file."
                    )
                audio_path = all_audio[0]
            else:
                audio_path = mp3_files[0]

            logger.info(
                f"[YouTubeParser] Audio downloaded: {audio_path.name} "
                f"({audio_path.stat().st_size // 1024} KB)"
            )

            # --- Transcribe ---
            logger.info(
                f"[YouTubeParser] Loading Whisper '{self.whisper_model}' model…"
            )
            model = whisper.load_model(self.whisper_model)

            transcribe_kwargs: dict = {"verbose": False, "fp16": False}
            if self.whisper_language:
                transcribe_kwargs["language"] = self.whisper_language

            logger.info("[YouTubeParser] Transcribing audio…")
            wresult = model.transcribe(str(audio_path), **transcribe_kwargs)

            full_text: str = wresult.get("text", "").strip()
            detected_lang: str = wresult.get("language", "unknown")
            segments = wresult.get("segments", [])

            logger.info(
                f"[YouTubeParser] Transcription complete. "
                f"language='{detected_lang}', chars={len(full_text)}"
            )

            # Optionally keep the audio
            if self.keep_audio:
                keep_path = Path.cwd() / audio_path.name
                shutil.copy2(audio_path, keep_path)
                logger.info(f"[YouTubeParser] Audio saved to: {keep_path}")

            return ParseResult(
                text=full_text,
                metadata={
                    "method": "whisper",
                    "whisper_model": self.whisper_model,
                    "detected_language": detected_lang,
                    "video_id": video_id,
                    "video_title": video_title,
                    "video_duration_seconds": video_duration,
                    "segments": [
                        {
                            "start": s["start"],
                            "end": s["end"],
                            "text": s["text"].strip(),
                        }
                        for s in segments
                    ],
                },
                source=source,
            )

        finally:
            if not self.keep_audio:
                shutil.rmtree(tmp_dir, ignore_errors=True)

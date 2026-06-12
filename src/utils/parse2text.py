"""
parse2text.py — Main entry-point for file-to-text extraction.

Supported sources
-----------------
+---------------------------+------------------------------------+
| File type                 | Extension(s)                       |
+===========================+====================================+
| PDF                       | .pdf                               |
+---------------------------+------------------------------------+
| PowerPoint                | .pptx .ppsx .pptm .ppsm .ppt .pps |
+---------------------------+------------------------------------+
| Audio                     | .mp3 .wav .m4a .ogg .flac          |
|                           | .aac .wma .opus .webm              |
+---------------------------+------------------------------------+
| YouTube URL               | youtube.com / youtu.be             |
+---------------------------+------------------------------------+

Usage example
-------------
::

    from src.utils.parse2text import Parse2Text

    p2t = Parse2Text()

    # --- PDF ---
    result = p2t.parse("report.pdf", output_dir="./out", delete_source=False)
    print(result.text[:200])

    # --- YouTube (transcript-first) ---
    result = p2t.parse(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        output_dir="./out",
    )
    print(result.metadata["method"])   # "transcript" or "whisper"

    # --- Audio with image extraction ---
    result = p2t.parse(
        "slides.pptx",
        output_dir="./out",
        extract_images=True,
        delete_source=True,
    )
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from loguru import logger

from src.utils.fileparsers.base import BaseParser, ParseResult
from src.utils.fileparsers.mp3_parser import MP3Parser
from src.utils.fileparsers.pdf_parser import PDFParser
from src.utils.fileparsers.ppt_parser import PPTParser
from src.utils.fileparsers.youtube_parser import YouTubeParser

# ---------------------------------------------------------------------------
# File extension → parser class mapping
# ---------------------------------------------------------------------------
_EXT_MAP: dict[str, type[BaseParser]] = {
    # PDF
    ".pdf": PDFParser,
    # PowerPoint (all variants)
    ".pptx": PPTParser,
    ".ppsx": PPTParser,
    ".pptm": PPTParser,
    ".ppsm": PPTParser,
    ".ppt":  PPTParser,
    ".pps":  PPTParser,
    # Audio
    ".mp3":  MP3Parser,
    ".wav":  MP3Parser,
    ".m4a":  MP3Parser,
    ".ogg":  MP3Parser,
    ".flac": MP3Parser,
    ".aac":  MP3Parser,
    ".wma":  MP3Parser,
    ".opus": MP3Parser,
    ".webm": MP3Parser,
}

_YT_PATTERN = re.compile(r"(?:youtube\.com|youtu\.be)")


class Parse2Text:
    """Orchestrator that routes any supported source to the correct parser.

    All parser instances are created lazily and cached for re-use.

    Args:
        pdf_kwargs:     Extra keyword arguments forwarded to :class:`PDFParser`.
        ppt_kwargs:     Extra keyword arguments forwarded to :class:`PPTParser`.
        mp3_kwargs:     Extra keyword arguments forwarded to :class:`MP3Parser`.
                        Common keys: ``model`` (Whisper size), ``language``, ``device``.
        youtube_kwargs: Extra keyword arguments forwarded to :class:`YouTubeParser`.
                        Common keys: ``preferred_languages``, ``whisper_model``,
                        ``keep_audio``.

    Example — override Whisper model::

        p2t = Parse2Text(mp3_kwargs={"model": "small", "language": "en"})
    """

    def __init__(
        self,
        pdf_kwargs: dict | None = None,
        ppt_kwargs: dict | None = None,
        mp3_kwargs: dict | None = None,
        youtube_kwargs: dict | None = None,
    ) -> None:
        self._pdf_kwargs = pdf_kwargs or {}
        self._ppt_kwargs = ppt_kwargs or {}
        self._mp3_kwargs = mp3_kwargs or {}
        self._youtube_kwargs = youtube_kwargs or {}

        # Lazy parser cache
        self._parsers: dict[type[BaseParser], BaseParser] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(
        self,
        source: str,
        *,
        output_dir: str | None = None,
        delete_source: bool = False,
        extract_images: bool = False,
    ) -> ParseResult:
        """Parse *source* and return a :class:`ParseResult`.

        Args:
            source:         Absolute / relative path to the file **or** a
                            YouTube URL (``youtube.com`` / ``youtu.be``).
            output_dir:     Directory where the extracted ``.txt`` (and
                            optional ``.png`` images) will be saved.
                            ``None`` → do not save any files.
            delete_source:  If ``True``, the original file is deleted after a
                            successful parse.  Has no effect for YouTube URLs.
            extract_images: When ``True``, ask the parser to extract images
                            (supported by :class:`PDFParser` and
                            :class:`PPTParser`).

        Returns:
            :class:`~src.utils.fileparsers.base.ParseResult`

        Raises:
            ValueError:       Unsupported file extension or unrecognised URL.
            FileNotFoundError: Source file does not exist.
        """
        parser = self._resolve_parser(source)
        label = getattr(parser, "label", type(parser).__name__)
        logger.info(f"[Parse2Text] Dispatching '{source}' → {label}")

        result = parser.parse(source, extract_images=extract_images)
        result.source = source

        # --- Save outputs ---
        if output_dir is not None:
            self._save_outputs(result, source, output_dir)

        # --- Delete source file ---
        if delete_source and not _YT_PATTERN.search(source):
            src_path = Path(source)
            if src_path.exists():
                src_path.unlink()
                logger.info(f"[Parse2Text] Deleted source file: {src_path}")
            else:
                logger.warning(
                    f"[Parse2Text] delete_source=True but file not found: {src_path}"
                )

        return result

    # ------------------------------------------------------------------
    # Parser shortcut methods
    # ------------------------------------------------------------------

    def parse_pdf(self, source: str, **kwargs) -> ParseResult:
        """Convenience wrapper — always uses :class:`PDFParser`."""
        return self._get_parser(PDFParser).parse(source, **kwargs)

    def parse_ppt(self, source: str, **kwargs) -> ParseResult:
        """Convenience wrapper — always uses :class:`PPTParser`."""
        return self._get_parser(PPTParser).parse(source, **kwargs)

    def parse_audio(self, source: str, **kwargs) -> ParseResult:
        """Convenience wrapper — always uses :class:`MP3Parser`."""
        return self._get_parser(MP3Parser).parse(source, **kwargs)

    def parse_youtube(self, source: str, **kwargs) -> ParseResult:
        """Convenience wrapper — always uses :class:`YouTubeParser`."""
        return self._get_parser(YouTubeParser).parse(source, **kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_parser(self, source: str) -> BaseParser:
        """Return the appropriate cached parser for *source*."""
        # YouTube URL?
        if _YT_PATTERN.search(source):
            return self._get_parser(YouTubeParser)

        # File extension?
        suffix = Path(source).suffix.lower()
        parser_cls = _EXT_MAP.get(suffix)
        if parser_cls is None:
            supported = sorted(_EXT_MAP.keys())
            raise ValueError(
                f"Unsupported file extension: '{suffix}'\n"
                f"Supported: {supported}\n"
                f"For YouTube videos, pass the full URL."
            )
        return self._get_parser(parser_cls)

    def _get_parser(self, cls: type[BaseParser]) -> BaseParser:
        """Return a cached parser instance, creating it if needed."""
        if cls not in self._parsers:
            kwargs = {
                PDFParser:     self._pdf_kwargs,
                PPTParser:     self._ppt_kwargs,
                MP3Parser:     self._mp3_kwargs,
                YouTubeParser: self._youtube_kwargs,
            }.get(cls, {})
            self._parsers[cls] = cls(**kwargs)
        return self._parsers[cls]

    @staticmethod
    def _save_outputs(
        result: ParseResult,
        source: str,
        output_dir: str,
    ) -> None:
        """Write text and images to *output_dir*."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Derive a base filename from the source
        if _YT_PATTERN.search(source):
            # Use video title from metadata if available, else video_id
            base = result.metadata.get(
                "video_title",
                result.metadata.get("video_id", "youtube_output"),
            )
            # Sanitise for filesystem
            base = re.sub(r'[\\/:*?"<>|]', "_", base)
        else:
            base = Path(source).stem

        # --- Write text ---
        txt_path = out / f"{base}.txt"
        txt_path.write_text(result.text, encoding="utf-8")
        logger.info(f"[Parse2Text] Text saved → {txt_path}")

        # --- Write images ---
        if result.images:
            saved = result.save_images(output_dir=out, prefix=base)
            logger.info(
                f"[Parse2Text] {len(saved)} image(s) saved → {out}"
            )

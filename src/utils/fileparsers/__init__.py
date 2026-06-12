"""
src/utils/fileparsers — File-to-text parsers package.

Exports
-------
BaseParser      Abstract base class for all parsers.
ParseResult     Dataclass returned by every parser.
PDFParser       PDF → text (+ page images).
PPTParser       PPT / PPTX / PPSX → text (+ slide thumbnails).
MP3Parser       MP3 / WAV / M4A / OGG / FLAC → text (Whisper).
YouTubeParser   YouTube URL → text (transcript API → Whisper fallback).
"""

from src.utils.fileparsers.base import BaseParser, ParseResult
from src.utils.fileparsers.mp3_parser import MP3Parser
from src.utils.fileparsers.pdf_parser import PDFParser
from src.utils.fileparsers.ppt_parser import PPTParser
from src.utils.fileparsers.youtube_parser import YouTubeParser

__all__ = [
    "BaseParser",
    "ParseResult",
    "PDFParser",
    "PPTParser",
    "MP3Parser",
    "YouTubeParser",
]

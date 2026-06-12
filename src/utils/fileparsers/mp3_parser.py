"""
mp3_parser.py — Transcribe audio files to text using OpenAI Whisper (local).

Supported input formats (anything ffmpeg can decode):
    .mp3, .wav, .m4a, .ogg, .flac, .aac, .wma, .opus

Requires:
    pip install openai-whisper
    # System dependency: ffmpeg
    #   Ubuntu/Debian: sudo apt install ffmpeg
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from src.utils.fileparsers.base import BaseParser, ParseResult

# Audio extensions this parser handles
_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus", ".webm",
}


class MP3Parser(BaseParser):
    """Transcribe audio files to text via OpenAI Whisper (runs locally, no API key).

    Args:
        model:    Whisper model name. Options: ``"tiny"``, ``"base"``,
                  ``"small"``, ``"medium"``, ``"large"``.
                  Larger models are more accurate but slower and use more RAM.
                  Default: ``"base"`` (~145 MB, good balance for most use-cases).
        language: ISO-639-1 language code hint (e.g. ``"en"``, ``"de"``).
                  ``None`` → auto-detect.
        device:   PyTorch device string: ``"cpu"`` or ``"cuda"``.
                  ``None`` → auto-select (CUDA if available, else CPU).

    Example::

        parser = MP3Parser(model="small", language="en")
        result = parser.parse("interview.mp3")
        print(result.text)
    """

    label = "MP3Parser"

    def __init__(
        self,
        model: str = "base",
        language: str | None = None,
        device: str | None = None,
    ) -> None:
        self.model_name = model
        self.language = language
        self.device = device
        self._model = None  # lazy-loaded on first parse()

    def _load_model(self):
        """Lazy-load Whisper model (only once per instance)."""
        if self._model is not None:
            return self._model
        try:
            import whisper  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "openai-whisper is required: pip install openai-whisper\n"
                "Also ensure ffmpeg is installed: sudo apt install ffmpeg"
            ) from exc

        logger.info(f"[MP3Parser] Loading Whisper '{self.model_name}' model…")
        kwargs = {}
        if self.device is not None:
            kwargs["device"] = self.device
        self._model = whisper.load_model(self.model_name, **kwargs)
        logger.info("[MP3Parser] Whisper model loaded.")
        return self._model

    # ------------------------------------------------------------------
    def parse(
        self,
        source: str,
        *,
        extract_images: bool = False,  # not applicable for audio
    ) -> ParseResult:
        """Transcribe an audio file to text.

        Args:
            source:         Absolute path to the audio file.
            extract_images: Ignored (audio files have no images).

        Returns:
            :class:`~src.utils.fileparsers.base.ParseResult` with ``text``
            containing the full transcription and ``metadata`` including
            detected language, duration, and per-segment timestamps.
        """
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {source}")

        suffix = path.suffix.lower()
        if suffix not in _AUDIO_EXTENSIONS:
            logger.warning(
                f"[MP3Parser] Extension '{suffix}' is not in the known list "
                f"{sorted(_AUDIO_EXTENSIONS)}. Attempting anyway."
            )

        model = self._load_model()

        transcribe_kwargs: dict = {
            "verbose": False,
            "fp16": False,  # safe default (fp32); set True for GPU speed-up
        }
        if self.language:
            transcribe_kwargs["language"] = self.language

        logger.info(f"[MP3Parser] Transcribing: {path.name}")
        result = model.transcribe(str(path), **transcribe_kwargs)

        segments = result.get("segments", [])
        full_text: str = result.get("text", "").strip()
        detected_lang: str = result.get("language", "unknown")
        duration_s: float = segments[-1]["end"] if segments else 0.0

        logger.info(
            f"[MP3Parser] Done. language='{detected_lang}', "
            f"duration={duration_s:.1f}s, chars={len(full_text)}"
        )

        return ParseResult(
            text=full_text,
            images=[],  # audio has no images
            metadata={
                "detected_language": detected_lang,
                "duration_seconds": round(duration_s, 2),
                "whisper_model": self.model_name,
                "segments": [
                    {
                        "start": s["start"],
                        "end": s["end"],
                        "text": s["text"].strip(),
                    }
                    for s in segments
                ],
                "source_size_bytes": path.stat().st_size,
            },
            source=str(source),
        )

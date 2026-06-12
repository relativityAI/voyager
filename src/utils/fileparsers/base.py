"""
base.py — Abstract base class and shared data structures for all file parsers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL.Image import Image


@dataclass
class ParseResult:
    """Unified result returned by every parser.

    Attributes:
        text:     Full extracted text content.
        images:   Optionally extracted images (PIL.Image objects).
                  Only populated when extract_images=True is supported.
        metadata: Arbitrary key/value pairs (page count, duration, …).
        source:   Original file path or URL that was parsed.
    """

    text: str = ""
    images: list["Image"] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def __bool__(self) -> bool:
        return bool(self.text)

    def __repr__(self) -> str:  # noqa: D401
        snippet = self.text[:80].replace("\n", " ")
        return (
            f"ParseResult(chars={len(self.text)}, "
            f"images={len(self.images)}, "
            f'snippet="{snippet}…")'
        )

    def save_text(self, output_path: str | Path, encoding: str = "utf-8") -> Path:
        """Write ``self.text`` to *output_path* and return the Path."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.text, encoding=encoding)
        return out

    def save_images(self, output_dir: str | Path, prefix: str = "image") -> list[Path]:
        """Save each PIL image to *output_dir* as ``<prefix>_001.png`` etc."""
        if not self.images:
            return []
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        for idx, img in enumerate(self.images, start=1):
            img_path = out_dir / f"{prefix}_{idx:03d}.png"
            img.save(img_path, format="PNG")
            saved.append(img_path)
        return saved


class BaseParser(ABC):
    """Abstract base class that all file parsers must implement.

    Subclasses are expected to be stateless; instantiate once and call
    :py:meth:`parse` as many times as needed.
    """

    #: Human-readable label used in log messages / error output.
    label: str = "BaseParser"

    @abstractmethod
    def parse(
        self,
        source: str,
        *,
        extract_images: bool = False,
    ) -> ParseResult:
        """Parse *source* and return a :class:`ParseResult`.

        Args:
            source:         Absolute path to the file (or a URL for
                            :class:`~src.utils.fileparsers.youtube_parser.YouTubeParser`).
            extract_images: When ``True``, attempt to extract embedded images.
                            Not every parser supports this; unsupported parsers
                            silently ignore the flag.

        Returns:
            A :class:`ParseResult` with at least ``text`` populated.
        """

    # ------------------------------------------------------------------
    # Shared utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _stem(source: str) -> str:
        """Return the file stem (no extension) for use as output filename."""
        return Path(source).stem

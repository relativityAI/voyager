"""
pdf_parser.py — Extract text (and optionally page images) from PDF files.

Requires:
    pip install pdfplumber Pillow
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Union

from loguru import logger

from src.utils.fileparsers.base import BaseParser, ParseResult


class PDFParser(BaseParser):
    """Parse PDF files into text using ``pdfplumber``.

    ``pdfplumber`` is built on ``pdfminer.six`` and handles complex layouts
    (multi-column, tables, rotated text) much better than simple readers.

    Args:
        page_separator: String inserted between page texts (default: ``"\\n\\n"``)
    """

    label = "PDFParser"

    def __init__(self, page_separator: str = "\n\n") -> None:
        self.page_separator = page_separator

    # ------------------------------------------------------------------
    def parse(
        self,
        source: str,
        *,
        extract_images: bool = False,
        pages: Union[list[int], None] = None,
    ) -> ParseResult:
        """Extract text (and optionally images) from a PDF file.

        Args:
            source:         Absolute path to the ``.pdf`` file.
            extract_images: When ``True``, render each page to a PIL image.
            pages:          0-based list of page indices to parse.
                            ``None`` → parse all pages.

        Returns:
            :class:`~src.utils.fileparsers.base.ParseResult`
        """
        try:
            import pdfplumber  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "pdfplumber is required: pip install pdfplumber"
            ) from exc

        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {source}")

        texts: list[str] = []
        images: list = []

        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            page_indices = pages if pages is not None else range(total_pages)

            for idx in page_indices:
                if idx >= total_pages:
                    logger.warning(f"[PDFParser] Page index {idx} out of range, skipping.")
                    continue

                page = pdf.pages[idx]

                # --- text ---
                page_text = page.extract_text() or ""
                texts.append(page_text)

                # --- images ---
                if extract_images:
                    try:
                        from PIL import Image  # noqa: PLC0415

                        # pdfplumber renders to PIL via .to_image()
                        page_img = page.to_image(resolution=150)
                        buf = io.BytesIO()
                        page_img.save(buf, format="PNG")
                        buf.seek(0)
                        images.append(Image.open(buf).copy())
                    except Exception as img_err:
                        logger.warning(
                            f"[PDFParser] Could not render page {idx} as image: {img_err}"
                        )

        full_text = self.page_separator.join(texts)

        return ParseResult(
            text=full_text,
            images=images,
            metadata={
                "total_pages": total_pages,
                "parsed_pages": len(list(page_indices)),
                "source_size_bytes": path.stat().st_size,
            },
            source=str(source),
        )

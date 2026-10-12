"""PDF structuring with PageIndex Flash, behind one read endpoint.

GET /documents/index?url=... fetches the PDF (URL or local path), parses it with
PageIndex Flash (LLM-free), caches the tree in ``document_indices`` keyed by
URL + content hash, and returns the tree. Cache hit returns immediately.
"""

import asyncio
import ctypes
import hashlib
import io
import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from loguru import logger
from pypdf import PdfReader
from requests import Session
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import DocumentIndex
from src.utils.helpers import utcnow
from src.utils.web import generate_fake_headers

from ._common import InvalidRequestError, ServiceUnavailableError, UpstreamError

# PageIndex's char-level parser keeps ~1.4MB of RSS per 1k extracted characters
# and never returns it, so an unbounded PDF OOMs the 512MB worker. Refuse
# oversized docs up front (cheap pypdf text pass decides) instead of crashing.
MAX_PDF_CHARS = int(os.getenv("VOYAGER_MAX_PDF_CHARS", "120000"))
MAX_PDF_BYTES = int(os.getenv("VOYAGER_MAX_PDF_BYTES", str(25 * 1024 * 1024)))

# One parse at a time: the agent fires several document reads concurrently and
# ~1.4MB/1k chars peaks overlap into OOM. Serialize to keep peak single-doc.
_parse_lock = asyncio.Lock()


def _release_memory() -> None:
    # glibc keeps freed arenas; the char-level model leaves ~100MB in them.
    # malloc_trim returns it to the OS. No-op off glibc.
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


def _pdf_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _fetch_pdf(url: str) -> bytes:
    # ponytail: accepts an http(s) URL or a local path (the endpoint's contract
    # says "PDF URL or path"). No zip archives, no redirects without a suffix,
    # no streaming for huge files — upgrade to urllib.parse + streaming once a
    # caller needs those.
    if url.startswith("file:"):
        url = url[7:]
    if not urlparse(url).scheme:
        try:
            data = Path(url).read_bytes()
        except OSError as exc:
            raise InvalidRequestError(f"Cannot read local PDF {url}: {exc}") from exc
    else:
        try:
            resp = Session().get(
                url, headers=generate_fake_headers(), timeout=60
            )
            resp.raise_for_status()
        except Exception as exc:
            raise UpstreamError(f"Failed to download PDF from {url}: {exc}")
        data = resp.content
    if not data or not data.startswith(b"%PDF-"):
        raise InvalidRequestError("URL did not return a PDF file")
    if len(data) > MAX_PDF_BYTES:
        raise InvalidRequestError(
            f"PDF is {len(data)} bytes; limit is {MAX_PDF_BYTES}. "
            "Raise VOYAGER_MAX_PDF_BYTES if the instance has headroom."
        )
    return data


def _page_index_flash(data: bytes) -> Dict[str, Any]:
    try:
        from pageindex.flash import page_index_flash
    except ImportError as exc:
        raise ServiceUnavailableError(
            "pageindex is not installed. Run: pip install pageindex"
        ) from exc

    # summary=False, optimize='merge' = deterministic, no LLM, no API key.
    return page_index_flash(io.BytesIO(data), summary=False, optimize="merge")


def _page_texts(data: bytes) -> list:
    """Per-page text: [{"page": N, "text": ...}]. Nodes' start/end_index are
    page numbers, so callers slice `text` by a node's range."""
    return [
        {"page": i + 1, "text": page.extract_text() or ""}
        for i, page in enumerate(PdfReader(io.BytesIO(data)).pages)
    ]


def _structure_and_text(data: bytes) -> tuple:
    """(PageIndex tree, per-page text). Text first (cheap) so the char guard
    rejects an oversized doc before the memory-hungry char-level parser runs."""
    text = _page_texts(data)
    chars = sum(len(t["text"]) for t in text)
    if chars > MAX_PDF_CHARS:
        raise InvalidRequestError(
            f"PDF has {chars} text characters; limit is {MAX_PDF_CHARS}. "
            "Raise VOYAGER_MAX_PDF_CHARS (needs ~1.4MB RAM per 1k chars)."
        )
    parsed = _page_index_flash(data)
    _release_memory()
    return parsed, text


async def _ensure_indexed(
    url: str, symbol: Optional[str], source: str
) -> Dict[str, Any]:
    """Fetch + parse, cache by URL+content hash. Returns the row payload
    (structure + full text). Cache hit skips parsing entirely."""
    data = await asyncio.to_thread(_fetch_pdf, url)
    pdf_hash = _pdf_hash(data)

    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(
                select(DocumentIndex).where(DocumentIndex.url == url)
            )
        ).scalar_one_or_none()
        if existing and existing.pdf_hash == pdf_hash and existing.page_index:
            logger.info(f"Serving cached page index for {url}")
            # Rows written before text existed: extract now (PDF already in
            # hand) and backfill, next call is a plain cache hit.
            text = existing.page_index.get("text")
            if not text:
                text = await asyncio.to_thread(_page_texts, data)
                existing.page_index = {**existing.page_index, "text": text}
                await session.commit()
            return {
                "id": existing.id,
                "url": url,
                "symbol": existing.symbol,
                "source": existing.source,
                "doc_title": existing.doc_title,
                "num_pages": existing.num_pages,
                "structure": existing.page_index.get("structure", []),
                "text": text,
                "cached": True,
            }

        async with _parse_lock:
            parsed, text = await asyncio.to_thread(_structure_and_text, data)
        structure = parsed.get("structure", [])
        doc_title = parsed.get("doc_title") or parsed.get("doc_name") or ""
        # flash doesn't report page count — count from the bytes we already have.
        num_pages = parsed.get("num_pages") or len(text)

        row = {
            "url": url,
            "pdf_hash": pdf_hash,
            "symbol": symbol,
            "source": source,
            "doc_title": doc_title,
            "num_pages": num_pages,
            "page_index": {"structure": structure, "text": text},
            "status": "parsed",
            "indexed_at": utcnow(),
        }
        result = await session.execute(
            pg_insert(DocumentIndex)
            .values(**row)
            .on_conflict_do_update(
                index_elements=["url"],
                set_={k: v for k, v in row.items() if k != "url"},
            )
            .returning(DocumentIndex.id)
        )
        await session.commit()
        return {
            "id": result.scalar_one(),
            "url": url,
            "symbol": symbol,
            "source": source,
            "doc_title": doc_title,
            "num_pages": num_pages,
            "structure": structure,
            "text": text,
            "cached": False,
        }


async def index_document(
    url: str, symbol: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    """Index a PDF (parse once, cache by URL+hash) and return the tree only."""
    doc = await _ensure_indexed(url, symbol, source)
    return {
        **{k: v for k, v in doc.items() if k not in ("text", "structure")},
        "page_index": {"structure": doc["structure"]},
    }


async def read_document(
    url: str, symbol: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    """Index + per-page text for the whole PDF. Prefer /documents/index +
    /documents/pages to fetch only the pages you need."""
    doc = await _ensure_indexed(url, symbol, source)
    return {
        **{k: v for k, v in doc.items() if k not in ("text", "structure")},
        "page_index": {"structure": doc["structure"], "text": doc["text"]},
    }


async def read_document_pages(
    url: str, pages: str, symbol: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    """Per-page text for the requested pages of an indexed PDF.

    ``pages`` is a 1-based spec: "3,5-9" or "1-3,7". Nodes in the tree carry
    start/end_index page numbers, so a caller slices by a node's range."""
    doc = await _ensure_indexed(url, symbol, source)
    wanted = _parse_pages(pages, doc["num_pages"])
    return {
        **{k: v for k, v in doc.items() if k not in ("text", "structure")},
        "page_index": {"structure": doc["structure"]},
        "pages": sorted(wanted),
        "text": [t for t in doc["text"] if t["page"] in wanted],
    }


def _parse_pages(spec: str, num_pages: int) -> set:
    pages: set = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo, hi = part.split("-", 1)
                pages.update(range(int(lo), int(hi) + 1))
            else:
                pages.add(int(part))
        except ValueError as exc:
            raise InvalidRequestError(f"Bad page spec {part!r}; use e.g. 3,5-9") from exc
    pages = {p for p in pages if 1 <= p <= num_pages}
    if not pages:
        raise InvalidRequestError(
            f"No requested page is within 1-{num_pages}"
        )
    return pages

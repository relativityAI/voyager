"""PDF ingestion: fetch, parse with PageIndex Flash, cache by URL+hash.

Write path only — invoked by the operator tooling. Document
reads (``src.services.documents``) serve the cache only.
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
from src.db.models import FilingDisclosure
from src.services._common import (
    InvalidRequestError,
    ServiceUnavailableError,
    UpstreamError,
)
from src.utils.helpers import utcnow
from src.utils.web import generate_fake_headers

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
        import pageindex.flash.api as _flash_api
        from pageindex.flash import page_index_flash
    except ImportError as exc:
        raise ServiceUnavailableError(
            "pageindex is not installed. Run: pip install pageindex"
        ) from exc

    # page_index_flash never forwards `workers`, so a doc of >=64 pages auto-
    # spawns cpu_count()-1 spawn processes — each re-imports pageindex/pdfium
    # and parses its own copy, ~550MB tree peak on 72 pages vs ~144MB one
    # process. That is what OOMs the 512MB worker. Force the sequential path;
    # pageindex documents parallel and sequential output as identical.
    _orig = _flash_api.extract_toc
    if not getattr(_orig, "_voyager_sequential", False):

        def _extract_toc_sequential(doc_handle, workers=None, **kwargs):
            return _orig(doc_handle, workers=1, **kwargs)

        _extract_toc_sequential._voyager_sequential = True
        _flash_api.extract_toc = _extract_toc_sequential

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


def _market_for(source: str) -> tuple:
    """(exchange, country) implied by a source code."""
    from src.services._common import SOURCE_COUNTRY

    return source, SOURCE_COUNTRY.get(source, "in")


async def ingest_document(
    url: str, symbol: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    """Fetch + parse a PDF and cache it in filings_disclosures by URL.

    Returns the tree payload (structure only, no text). Re-ingesting a URL
    whose bytes are unchanged is a cache hit; changed bytes re-parse and
    upsert.
    """
    data = await asyncio.to_thread(_fetch_pdf, url)
    pdf_hash = _pdf_hash(data)
    source = source.upper()
    exchange, country = _market_for(source)

    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(
                select(FilingDisclosure).where(
                    FilingDisclosure.source == source,
                    FilingDisclosure.url == url,
                )
            )
        ).scalar_one_or_none()
        if existing and existing.pdf_index:
            if existing.pdf_index.get("hash") == pdf_hash:
                logger.info(f"Serving cached page index for {url}")
                text = existing.pdf_index.get("text")
                if not text:
                    # Rows written before text existed: extract now (PDF
                    # already in hand) and backfill.
                    text = await asyncio.to_thread(_page_texts, data)
                    existing.pdf_index = {**existing.pdf_index, "text": text}
                    await session.commit()
                return {
                    "id": existing.id,
                    "url": url,
                    "symbol": existing.symbol,
                    "source": source,
                    "doc_title": existing.title,
                    "num_pages": len(text),
                    "structure": existing.pdf_index.get("structure", []),
                    "cached": True,
                }

        async with _parse_lock:
            parsed, text = await asyncio.to_thread(_structure_and_text, data)
        structure = parsed.get("structure", [])
        doc_title = parsed.get("doc_title") or parsed.get("doc_name") or ""
        # flash doesn't report page count — count from the bytes we already have.
        num_pages = parsed.get("num_pages") or len(text)

        row = {
            "symbol": symbol,
            "exchange": exchange,
            "country": country,
            "source": source,
            "filing_type": "document",
            "url": url,
            "title": doc_title,
            "pdf_index": {"hash": pdf_hash, "structure": structure, "text": text},
            "created_at": utcnow(),
        }
        if existing:
            for k, v in row.items():
                setattr(existing, k, v)
            result_id = existing.id
        else:
            result = await session.execute(
                pg_insert(FilingDisclosure)
                .values(**row)
                .returning(FilingDisclosure.id)
            )
            result_id = result.scalar_one()
        await session.commit()
        return {
            "id": result_id,
            "url": url,
            "symbol": symbol,
            "source": source,
            "doc_title": doc_title,
            "num_pages": num_pages,
            "structure": structure,
            "cached": False,
        }

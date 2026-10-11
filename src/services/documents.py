"""PDF structuring with PageIndex Flash, behind one read endpoint.

GET /documents/index?url=... fetches the PDF (URL or local path), parses it with
PageIndex Flash (LLM-free), caches the tree in ``document_indices`` keyed by
URL + content hash, and returns the tree. Cache hit returns immediately.
"""

import asyncio
import hashlib
import io
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
    return _page_index_flash(data), _page_texts(data)


async def read_document(
    url: str, symbol: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    """PageIndex tree + per-page text for a PDF, parsed once, cached by URL+hash.

    ponytail: parse runs inline in the request (PageIndex Flash is local, ~2s
    for a 15-page PDF). No job queue, no polling, no document id — the caller
    gets the tree back directly. Ceiling: a huge PDF can outlast a gateway
    timeout; if that ever bites, re-add the async job path behind the same URL.
    """
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
            if not existing.page_index.get("text"):
                existing.page_index = {
                    **existing.page_index,
                    "text": await asyncio.to_thread(_page_texts, data),
                }
                await session.commit()
            return _serialize(existing, cached=True)

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
            **row,
            "page_index": {"structure": structure},
            "id": result.scalar_one(),
            "text": text,
            "cached": False,
        }


def _serialize(doc: DocumentIndex, *, cached: bool) -> Dict[str, Any]:
    page_index = doc.page_index or {}
    return {
        "id": doc.id,
        "url": doc.url,
        "symbol": doc.symbol,
        "source": doc.source,
        "doc_title": doc.doc_title,
        "num_pages": doc.num_pages,
        "page_index": {"structure": page_index.get("structure", [])},
        "text": page_index.get("text", []),
        "cached": cached,
    }

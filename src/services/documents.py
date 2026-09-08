"""PDF structuring with PageIndex Flash.

Builds a hierarchical tree index of a PDF's layout (headings, sections) on a
fully local, LLM-free path (``summary=False, optimize='merge'``), caches it in
the ``document_indices`` table keyed by URL + content hash so the same PDF is
only parsed once. Non-text (scanned) PDFs fail with a clear error — OCR is not
supported (see options decision).
"""

import asyncio
import hashlib
import io
from datetime import datetime
from typing import Any, Dict

from loguru import logger
from requests import Session
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import DocumentIndex
from src.utils.web import generate_fake_headers

from ._common import (
    InvalidRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UpstreamError,
)


def _pdf_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _fetch_pdf(url: str) -> bytes:
    try:
        resp = Session().get(
            url, headers=generate_fake_headers(), timeout=60
        )
        resp.raise_for_status()
    except Exception as exc:
        raise UpstreamError(f"Failed to download PDF from {url}: {exc}")
    if not resp.content or not resp.content.startswith(b"%PDF-"):
        raise InvalidRequestError("URL did not return a PDF file")
    return resp.content


def _page_index_flash(data: bytes) -> Dict[str, Any]:
    try:
        from pageindex.flash import page_index_flash
    except ImportError as exc:
        raise ServiceUnavailableError(
            "pageindex is not installed. Run: pip install pageindex"
        ) from exc

    # summary=False, optimize='merge' = deterministic, no LLM, no API key.
    return page_index_flash(io.BytesIO(data), summary=False, optimize="merge")


async def _parse_pdf_in_thread(data: bytes) -> Dict[str, Any]:
    return await asyncio.to_thread(_page_index_flash, data)


async def parse_document(task_args: Dict[str, Any]) -> Dict[str, Any]:
    """Run inside a job: download the PDF, build the PageIndex tree, cache it."""
    url = task_args.get("url")
    if not url:
        raise InvalidRequestError("task_args must include a PDF url")

    data = await asyncio.to_thread(_fetch_pdf, url)
    pdf_hash = _pdf_hash(data)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(DocumentIndex).where(DocumentIndex.url == url)
        )
        existing = result.scalar_one_or_none()
        if existing and existing.pdf_hash == pdf_hash and existing.page_index:
            logger.info(f"Serving cached page index for {url}")
            return {"cached": True, "document_id": existing.id}

        try:
            parsed = await _parse_pdf_in_thread(data)
        except Exception as exc:
            result = await session.execute(
                select(DocumentIndex).where(DocumentIndex.url == url)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.status = "failed"
                existing.error = str(exc)
            else:
                session.add(
                    DocumentIndex(
                        url=url,
                        pdf_hash=pdf_hash,
                        status="failed",
                        error=str(exc),
                    )
                )
            await session.commit()
            raise ServiceUnavailableError(str(exc)) from exc

        structure = parsed.get("structure", [])
        doc_title = parsed.get("doc_title") or parsed.get("doc_name") or ""

        row = {
            "url": url,
            "pdf_hash": pdf_hash,
            "symbol": task_args.get("symbol"),
            "source": task_args.get("source", "nse"),
            "doc_title": doc_title,
            "num_pages": parsed.get("num_pages"),
            "page_index": {"structure": structure},
            "status": "parsed",
            "indexed_at": datetime.utcnow(),
        }
        stmt = (
            pg_insert(DocumentIndex)
            .values(**row)
            .on_conflict_do_update(
                index_elements=["url"],
                set_={k: v for k, v in row.items() if k != "url"},
            )
        )
        await session.execute(stmt)
        await session.commit()
        result = await session.execute(
            select(DocumentIndex).where(DocumentIndex.url == url)
        )
        saved = result.scalar_one()
        return {"cached": False, "document_id": saved.id}


async def get_document_index(document_id: int) -> Dict[str, Any]:
    """Return the cached page index for a document, or 404 if not parsed."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(DocumentIndex).where(DocumentIndex.id == document_id)
        )
        doc = result.scalar_one_or_none()
    if doc is None:
        raise NotFoundError(f"No document with id {document_id}")
    if doc.status != "parsed" or not doc.page_index:
        raise NotFoundError(f"Document {document_id} has not been parsed yet")
    return {
        "id": doc.id,
        "url": doc.url,
        "symbol": doc.symbol,
        "source": doc.source,
        "doc_title": doc.doc_title,
        "num_pages": doc.num_pages,
        "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
        "page_index": doc.page_index,
    }

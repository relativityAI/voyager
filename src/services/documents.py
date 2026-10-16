"""PDF reads from the document cache (DB-only).

Fetching + parsing PDFs into ``document_indices`` lives in
``src.ingest.documents`` and runs via the ``voyager ingest documents`` CLI.
Reads here serve cache hits only; a missing cache row is a 404 pointing at
the ingest command.
"""

from typing import Any, Dict, Optional

from sqlalchemy import select

from src.db.engine import get_session_factory
from src.db.models import DocumentIndex

from ._common import InvalidRequestError, NotFoundError


async def _load_indexed(url: str) -> Optional[DocumentIndex]:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(DocumentIndex).where(DocumentIndex.url == url)
            )
        ).scalar_one_or_none()


async def read_document(
    url: str, symbol: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    """Per-page text for a whole PDF previously cached by
    ``voyager ingest documents <url>``."""
    existing = await _load_indexed(url)
    if not existing or not existing.page_index:
        raise NotFoundError(
            f"Document not indexed; run 'voyager ingest documents {url}'"
        )
    page_index = existing.page_index
    return {
        "id": existing.id,
        "url": url,
        "symbol": existing.symbol,
        "source": existing.source,
        "doc_title": existing.doc_title,
        "num_pages": existing.num_pages,
        "cached": True,
        "page_index": {
            "structure": page_index.get("structure", []),
            "text": page_index.get("text") or [],
        },
    }


async def read_document_pages(
    url: str, pages: str, symbol: Optional[str] = None, source: str = "nse"
) -> Dict[str, Any]:
    """Per-page text for the requested pages of a cached PDF.

    ``pages`` is a 1-based spec: "3,5-9" or "1-3,7". Nodes in the tree carry
    start/end_index page numbers, so a caller slices by a node's range."""
    existing = await _load_indexed(url)
    if not existing or not existing.page_index:
        raise NotFoundError(
            f"Document not indexed; run 'voyager ingest documents {url}'"
        )
    page_index = existing.page_index
    wanted = _parse_pages(pages, existing.num_pages)
    return {
        "id": existing.id,
        "url": url,
        "symbol": existing.symbol,
        "source": existing.source,
        "doc_title": existing.doc_title,
        "num_pages": existing.num_pages,
        "cached": True,
        "page_index": {"structure": page_index.get("structure", [])},
        "pages": sorted(wanted),
        "text": [t for t in page_index.get("text") or [] if t["page"] in wanted],
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

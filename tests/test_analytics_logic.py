"""Unit tests for analytics logic: job dispatch."""

from unittest.mock import patch

import pytest

from src.jobs import _run_task
from src.tools.social.youtube import _parse_caption_json


class TestTaskDispatch:
    @pytest.mark.asyncio
    async def test_unknown_task_raises(self):
        with pytest.raises(ValueError, match="Unknown task"):
            await _run_task("no.such.task", {})


class TestTranscriptParser:
    def test_parse_caption_json(self):
        out = _parse_caption_json(
            {
                "events": [
                    {
                        "tStartMs": 0,
                        "dDurationMs": 1500,
                        "segs": [{"utf8": "Hello "}, {"utf8": "world"}],
                    },
                    {
                        "tStartMs": 1500,
                        "dDurationMs": 900,
                        "segs": [{"utf8": "Second"}],
                    },
                    {"tStartMs": 3000, "dDurationMs": 0, "segs": []},
                ]
            }
        )
        assert out == [
            {"text": "Hello world", "start": 0.0, "duration": 1.5},
            {"text": "Second", "start": 1.5, "duration": 0.9},
        ]

    def test_parse_caption_json_skips_empty(self):
        assert _parse_caption_json({"events": [{"segs": []}]}) == []


class TestPDFReading:
    @pytest.fixture
    def pdf_bytes(self):
        from io import BytesIO

        from pypdf import PdfWriter

        buf = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(buf)
        return buf.getvalue()

    def test_fetch_pdf_reads_local_path(self, tmp_path, pdf_bytes):
        from src.services.documents import _fetch_pdf

        path = tmp_path / "doc.pdf"
        path.write_bytes(pdf_bytes)
        assert _fetch_pdf(str(path)) == pdf_bytes
        # file:// URLs are the natural spelling of a path from an agent
        assert _fetch_pdf(path.as_uri()) == pdf_bytes

    def test_fetch_pdf_missing_local_file(self, tmp_path):
        from src.services._common import InvalidRequestError
        from src.services.documents import _fetch_pdf

        with pytest.raises(InvalidRequestError, match="Cannot read local PDF"):
            _fetch_pdf(str(tmp_path / "nope.pdf"))

    def test_fetch_pdf_rejects_non_pdf_bytes(self, tmp_path):
        from src.services._common import InvalidRequestError
        from src.services.documents import _fetch_pdf

        path = tmp_path / "html.pdf"
        path.write_bytes(b"<html>error page</html>")
        with pytest.raises(InvalidRequestError, match="did not return a PDF"):
            _fetch_pdf(str(path))

    def test_page_texts_and_structure(self, pdf_bytes):
        from src.services.documents import _page_texts, _structure_and_text

        assert _page_texts(pdf_bytes) == [{"page": 1, "text": ""}]
        with patch(
            "src.services.documents._page_index_flash",
            return_value={"structure": [{"title": "T"}]},
        ):
            parsed, text = _structure_and_text(pdf_bytes)
        assert parsed["structure"] == [{"title": "T"}]
        assert len(text) == 1

    def test_read_pdf_rejects_extensionless_url(self):
        # must raise a clear error, not return None (and not hit console.error)
        from src.utils.helpers import read_pdf

        with pytest.raises(ValueError, match="no \\.pdf/\\.zip extension"):
            read_pdf("https://example.com/report")

    def test_read_pdf_returns_page_text(self, tmp_path, pdf_bytes):
        from src.utils.helpers import read_pdf

        path = tmp_path / "doc.pdf"
        path.write_bytes(pdf_bytes)
        # one blank page -> a single newline, no exception
        assert read_pdf(str(path)) == "\n"

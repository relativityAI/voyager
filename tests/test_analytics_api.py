"""Endpoint tests for the analytics suite: DCF, documents, news, social, sentiment."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from fastapi.testclient import TestClient

    from api import app

    client = TestClient(app)
    HAS_API = True
except ImportError:
    HAS_API = False
    pytest.skip("Skipping API tests: import issue", allow_module_level=True)


DCF_METRICS = {
    "free_cash_flow_per_share": 10.0,
    "current_price": 100.0,
    "revenue_growth": 10.0,
    "symbol": "TEST",
}


class TestDCF:
    def test_dcf_returns_valuation(self):
        with patch(
            "src.services.dcf.financial_metrics",
            new=AsyncMock(return_value=DCF_METRICS),
        ):
            resp = client.get("/dcf?symbol=TEST")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TEST"
        # 10 @ g=10%, r=10.6% -> a positive intrinsic value
        assert data["intrinsic_value_per_share"] > 0
        assert data["assumptions"]["fcf_per_share"] == 10.0

    def test_dcf_rejects_bad_years(self):
        resp = client.get("/dcf?symbol=TEST&years=0")
        assert resp.status_code == 422
        resp = client.get("/dcf?symbol=TEST&years=30")
        assert resp.status_code == 422

    def test_dcf_discount_must_exceed_terminal_growth(self):
        with patch(
            "src.services.dcf.financial_metrics",
            new=AsyncMock(return_value=DCF_METRICS),
        ):
            resp = client.get(
                "/dcf?symbol=TEST&discount_rate=0.02&terminal_growth_rate=0.04"
            )
        assert resp.status_code == 400


class TestDocuments:
    def test_parse_submits_task(self):
        job = MagicMock()
        job.job_id = "job-123"
        job.status = "queued"
        with patch("api.submit_task", new=AsyncMock(return_value=job)):
            resp = client.post("/documents/parse?url=https://x.com/a.pdf")
        assert resp.status_code == 202
        assert resp.json()["job_id"] == "job-123"

    def test_parse_requires_url(self):
        with patch("api.submit_task", new=AsyncMock()) as m:
            resp = client.post("/documents/parse")
        assert resp.status_code == 422
        m.assert_not_awaited()

    def test_get_index_not_parsed(self):
        from src.services._common import NotFoundError

        with patch(
            "api.get_document_index",
            new=AsyncMock(side_effect=NotFoundError("not parsed")),
        ):
            resp = client.get("/documents/123/index")
        assert resp.status_code == 404

    def test_get_index_cached(self):
        with patch(
            "api.get_document_index",
            new=AsyncMock(
                return_value={"id": 1, "page_index": {"structure": []}, "status": "parsed"}
            ),
        ):
            resp = client.get("/documents/1/index")
        assert resp.status_code == 200
        assert resp.json()["page_index"] == {"structure": []}


class TestNews:
    def test_stories(self):
        with patch(
            "api.get_news_stories",
            new=AsyncMock(return_value={"country": "in", "stories": []}),
        ):
            resp = client.get("/news/stories?country=in")
        assert resp.status_code == 200
        assert resp.json()["stories"] == []

    def test_stories_rejects_bad_country(self):
        resp = client.get("/news/stories?country=xx")
        assert resp.status_code == 400

    def test_ticker(self):
        with patch(
            "api.get_ticker_mentions",
            new=AsyncMock(return_value={"symbol": "TEST", "stories": []}),
        ):
            resp = client.get("/news/ticker?symbol=TEST&country=in")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "TEST"


class TestSocial:
    def test_reddit(self):
        with patch(
            "api.get_reddit",
            new=AsyncMock(return_value={"query": "TEST", "posts": []}),
        ):
            resp = client.get("/social/reddit?query=TEST")
        assert resp.status_code == 200
        assert resp.json()["posts"] == []

    def test_youtube_search(self):
        with patch(
            "api.get_youtube_search",
            new=AsyncMock(return_value={"query": "TEST", "videos": []}),
        ):
            resp = client.get("/social/youtube/search?query=TEST")
        assert resp.status_code == 200

    def test_youtube_transcript(self):
        with patch(
            "api.get_youtube_transcript",
            new=AsyncMock(return_value={"video_id": "abc", "text": "..."}),
        ):
            resp = client.get("/social/youtube/transcript?video_id=abc")
        assert resp.status_code == 200
        assert resp.json()["video_id"] == "abc"


class TestSentiment:
    def test_requires_input(self):
        resp = client.post("/sentiment/management")
        assert resp.status_code == 400

    def test_submits_job(self):
        job = MagicMock()
        job.job_id = "sent-1"
        job.status = "queued"
        with patch("api.submit_task", new=AsyncMock(return_value=job)):
            resp = client.post("/sentiment/management?text=hello")
        assert resp.status_code == 202
        assert resp.json()["job_id"] == "sent-1"

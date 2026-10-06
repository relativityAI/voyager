"""Endpoint tests for the analytics suite: documents, news, social."""

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
        # country is a spec enum now -> FastAPI 422 problem+json
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation_error"

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


class TestSearch:
    def test_search_substring(self):
        with patch(
            "api.search_symbols",
            new=AsyncMock(
                return_value={"query": "relian", "source": "NSE", "count": 1,
                              "results": [{"symbol": "RELIANCE", "has_data": True}]}
            ),
        ):
            resp = client.get("/search?q=relian")
        assert resp.status_code == 200
        assert resp.json()["results"][0]["symbol"] == "RELIANCE"

    def test_search_requires_q(self):
        resp = client.get("/search")
        assert resp.status_code == 422

    def test_search_min_length(self):
        resp = client.get("/search?q=a")
        assert resp.status_code == 422


class TestCachingAndPagination:
    def test_news_gets_etag_and_cache_control(self):
        with patch(
            "api.get_news_stories",
            new=AsyncMock(return_value={"country": "in", "stories": []}),
        ):
            resp = client.get("/news/stories?country=in")
        assert resp.status_code == 200
        assert resp.headers.get("Cache-Control") == "public, max-age=120"
        assert resp.headers.get("ETag", "").startswith('"')

    def test_conditional_get_returns_304(self):
        body = {"country": "in", "stories": []}
        with patch("api.get_news_stories", new=AsyncMock(return_value=body)):
            first = client.get("/news/stories?country=in")
            second = client.get(
                "/news/stories?country=in",
                headers={"If-None-Match": first.headers["ETag"]},
            )
        assert second.status_code == 304

    def test_conditional_get_tolerates_weak_etag(self):
        """Proxies like Render's rewrite ETags to W/ form; 304 must still fire."""
        body = {"country": "in", "stories": []}
        with patch("api.get_news_stories", new=AsyncMock(return_value=body)):
            first = client.get("/news/stories?country=in")
            weak = 'W/' + first.headers["ETag"]
            second = client.get(
                "/news/stories?country=in",
                headers={"If-None-Match": weak},
            )
        assert second.status_code == 304

    def test_non_cacheable_paths_unaffected(self):
        with patch(
            "api.search_symbols",
            new=AsyncMock(return_value={"query": "x", "results": [], "count": 0}),
        ):
            resp = client.get("/search?q=xx")
        assert resp.status_code == 200
        assert "Cache-Control" not in resp.headers or "max-age" not in (
            resp.headers.get("Cache-Control") or ""
        )


class TestStatementPagination:
    def test_statement_offset_and_total(self):
        rows = [{"symbol": "T", "period_end_date": f"2026-0{i}-30"} for i in range(1, 5)]
        with patch(
            "api.get_statement_data",
            new=AsyncMock(
                return_value={
                    "income_statements": rows[2:],
                    "pagination": {"total": 4, "offset": 2, "limit": 2, "returned": 2},
                }
            ),
        ) as m:
            resp = client.get(
                "/financials/income-statements?symbol=T&limit=2&offset=2"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"] == {
            "total": 4, "offset": 2, "limit": 2, "returned": 2
        }
        # offset must be forwarded to the service
        assert m.await_args.args[7] == 2


class TestFieldFilterAndBatch:
    def test_fields_keeps_meta_and_requested(self):
        full = {
            "symbol": "T",
            "filing_type": "ttm",
            "price_data": "live",
            "price_to_earnings_ratio": 20.0,
            "return_on_equity": 12.0,
            "revenue_growth_annual": 5.0,
            "enterprise_value": 1.0,
        }
        with patch(
            "api.financial_metrics", new=AsyncMock(return_value=full)
        ):
            resp = client.get(
                "/financial-metrics?symbol=T&fields=price_to_earnings_ratio"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "symbol": "T",
            "filing_type": "ttm",
            "price_data": "live",
            "price_to_earnings_ratio": 20.0,
        }

    def test_batch_returns_per_symbol_results(self):
        full = {"symbol": "X", "filing_type": "ttm", "price_to_earnings_ratio": 20.0}
        with patch("api.financial_metrics", new=AsyncMock(return_value=full)):
            resp = client.get(
                "/financial-metrics/batch?symbols=X,Y&fields=price_to_earnings_ratio"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["metrics"]["X"]["price_to_earnings_ratio"] == 20.0

    def test_batch_survives_symbol_errors(self):
        from src.services._common import NotFoundError

        async def _fail(sym, *a, **kw):
            if sym == "BAD":
                raise NotFoundError("no data")
            return {"symbol": sym, "filing_type": "ttm"}

        with patch("api.financial_metrics", new=AsyncMock(side_effect=_fail)):
            resp = client.get("/financial-metrics/batch?symbols=OK,BAD")
        assert resp.status_code == 200
        body = resp.json()
        assert body["metrics"]["OK"]["symbol"] == "OK"
        assert body["metrics"]["BAD"]["status"] == 404

    def test_batch_rejects_too_many(self):
        syms = ",".join(f"S{i}" for i in range(11))
        resp = client.get(f"/financial-metrics/batch?symbols={syms}")
        assert resp.status_code == 400


class TestJobCancel:
    def test_cancel_unknown_job_404(self):
        with patch("api.cancel_job", new=AsyncMock(side_effect=ValueError("not found"))):
            resp = client.request("DELETE", "/pull/jobs/nope")
        assert resp.status_code == 404

    def test_cancel_finished_job_409(self):
        from src.jobs import JobNotCancellable

        with patch("api.cancel_job", new=AsyncMock(side_effect=JobNotCancellable("already done"))):
            resp = client.request("DELETE", "/pull/jobs/j1")
        assert resp.status_code == 409

    def test_cancel_other_keys_job_403(self):
        with patch("api.cancel_job", new=AsyncMock(side_effect=PermissionError("other key"))):
            resp = client.request("DELETE", "/pull/jobs/j1")
        assert resp.status_code == 403

    def test_cancel_ok(self):
        job = MagicMock()
        job.to_public_dict.return_value = {"job_id": "j1", "status": "failed"}
        with patch("api.cancel_job", new=AsyncMock(return_value=job)):
            resp = client.request("DELETE", "/pull/jobs/j1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

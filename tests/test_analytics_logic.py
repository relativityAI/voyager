"""Unit tests for analytics logic: sentiment text processing, job dispatch."""

import pytest

from src.jobs import _run_task
from src.services.sentiment import _extract_json, _merge, chunk_text


class TestSentimentHelpers:
    def test_chunk_text_splits_bounded_chunks(self):
        text = "Sentence. " * 5000  # ~50k chars -> capped at MAX_CHUNKS
        chunks = chunk_text(text)
        assert 0 < len(chunks) <= 20
        for c in chunks:
            assert 0 < len(c) <= 8000

    def test_chunk_text_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_merge_aggregates(self):
        merged = _merge(
            [
                {
                    "claims": [{"quote": "q1"}], "boilerplate": [], "hedging": [],
                    "tone_score": 2, "factual_density": 0.8, "summary": "S1",
                },
                {
                    "claims": [], "boilerplate": ["b"], "hedging": ["h"],
                    "tone_score": 1, "factual_density": None, "summary": "S2",
                },
            ]
        )
        assert merged["tone"] == "positive"
        assert merged["tone_score"] == 2  # (2+1)/2 = 1.5 rounds half-up
        assert len(merged["claims"]) == 1
        assert merged["boilerplate"] == ["b"]
        assert merged["factual_density"] == 0.8
        assert merged["summary"] == "S1 S2"

    def test_extract_json_with_fences(self):
        raw = '```json\n{"a": 1}\n```'
        assert _extract_json(raw) == {"a": 1}

    def test_extract_json_embedded(self):
        raw = 'prefix {"a": [1,2]} suffix'
        assert _extract_json(raw) == {"a": [1, 2]}

    def test_extract_json_invalid_raises(self):
        from src.services._common import ServiceUnavailableError

        with pytest.raises(ServiceUnavailableError):
            _extract_json("not json")


class TestTaskDispatch:
    @pytest.mark.asyncio
    async def test_unknown_task_raises(self):
        with pytest.raises(ValueError, match="Unknown task"):
            await _run_task("no.such.task", {})

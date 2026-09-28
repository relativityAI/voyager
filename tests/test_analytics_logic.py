"""Unit tests for analytics logic: job dispatch."""

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

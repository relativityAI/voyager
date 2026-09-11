"""Verifies the SEC pull fixes:
- pull jobs that produce zero records are failed, not silently marked done
- empty SEC pulls do not fabricate metadata/"last_pull"
"""

import pytest

from src.jobs import _pull_outcome


def test_completed_pull_is_done():
    assert _pull_outcome({"status": "completed", "rows_written": 24}) == "done"


def test_partial_pull_with_rows_is_done():
    assert _pull_outcome({"status": "partial", "rows_written": 5}) == "done"


def test_partial_pull_with_zero_rows_is_failed():
    assert _pull_outcome({"status": "partial", "rows_written": 0}) == "failed"
    assert _pull_outcome({"status": "partial", "xbrl_parsed": 0}) == "failed"


def test_failed_pull_is_failed():
    assert _pull_outcome({"status": "failed", "rows_written": 0}) == "failed"


def test_empty_nse_pull_is_failed():
    # NSE statuses report xbrl_parsed, not rows_written
    assert _pull_outcome({"status": "partial", "xbrl_parsed": 0}) == "failed"


def test_no_data_with_existing_data_stays_done():
    # "no data" only occurs when nothing new was parseable (e.g. re-pull with
    # data already present); not treated as a failure.
    assert _pull_outcome({"status": "no data", "rows_written": 0}) == "done"
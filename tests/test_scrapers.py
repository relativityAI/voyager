"""Transport-level tests for the stealth scraping layer (src/scrapers/).

Covers the research-validated anti-detection behaviours (D-01..D-11):
fingerprint stability, throttling, cookie persistence + fallback, and the
prime/retry/validation semantics of StealthSession.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.scrapers.config import SourceConfig
from src.scrapers.cookies import (
    FileCookieStore,
    MemoryCookieStore,
    create_cookie_store,
)
from src.scrapers.fingerprint import Fingerprint, get_impersonate
from src.scrapers.session import (
    BlockedResponse,
    CookieError,
    SessionExhausted,
    StealthSession,
)
from src.scrapers.throttle import TokenBucketThrottle, get_throttle, reset_throttles


def make_config(**overrides):
    defaults = dict(
        name="test",
        country="in",
        base_url="https://example.com",
        warmup_url="https://example.com/warmup",
        warmup_fallbacks=[],
        endpoints={},
        referer_base="https://example.com/get-quotes?symbol={symbol}",
        calls_per_second=1000.0,
        retries=3,
        backoff_base=0.0,
        cookie_store="memory",
    )
    defaults.update(overrides)
    return SourceConfig(**defaults)


# ------------------------------------------------------------------ fingerprint


def test_impersonate_default_and_env(monkeypatch):
    assert Fingerprint().impersonate == "chrome131"
    monkeypatch.setenv("TEST_IMPERSONATE", "firefox133")
    assert get_impersonate("test") == "firefox133"
    monkeypatch.delenv("TEST_IMPERSONATE")
    assert get_impersonate("test") == "chrome131"


def test_fingerprint_api_headers_have_referer_and_sec_fetch():
    fp = Fingerprint(impersonate="chrome131")
    page = fp.page_load_headers()
    api = fp.api_headers("https://example.com/get-quotes?symbol=TCS")

    assert "User-Agent" in page
    assert page["User-Agent"] == api["User-Agent"]
    assert api["Referer"] == "https://example.com/get-quotes?symbol=TCS"
    assert api["sec-fetch-dest"] == "empty"
    assert api["sec-fetch-mode"] == "cors"
    assert api["sec-fetch-site"] == "same-origin"


def test_fingerprint_ua_stable_across_calls():
    fp = Fingerprint()
    ua1 = fp.page_load_headers()["User-Agent"]
    ua2 = fp.api_headers(None)["User-Agent"]
    assert ua1 == ua2


# ------------------------------------------------------------------ throttle


def test_token_bucket_allows_burst_then_throttles():
    throttle = TokenBucketThrottle(calls_per_second=100.0, burst=2)
    throttle.wait()
    throttle.wait()
    start = time.monotonic()
    throttle.wait()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.008  # 100 req/s -> ~10ms per token (jittered down 20%)
    throttle.wait()


def test_get_throttle_is_global_per_source():
    reset_throttles()
    a = get_throttle("t1", 10.0, 2)
    b = get_throttle("t1", 10.0, 2)
    assert a is b
    reset_throttles()
    c = get_throttle("t1", 10.0, 2)
    assert c is not a


# ------------------------------------------------------------------ cookies


def test_memory_cookie_store_roundtrip():
    store = MemoryCookieStore()
    store.save({"nsit": "abc"})
    assert store.load() == {"nsit": "abc"}


def test_file_cookie_store_roundtrip(tmp_path):
    store = FileCookieStore(path=str(tmp_path / "cookies.json"))
    store.save({"nsit": "abc"})
    assert store.load() == {"nsit": "abc"}


def test_file_cookie_store_degrades_to_memory_on_write_failure(tmp_path):
    path = tmp_path / "cookies.json"
    path.mkdir()  # write to a directory fails
    store = FileCookieStore(path=str(path))
    store.save({"nsit": "abc"})
    assert store.load() == {}


def test_file_cookie_store_missing_file_loads_empty(tmp_path):
    store = FileCookieStore(path=str(tmp_path / "nope.json"))
    assert store.load() == {}


def test_create_cookie_store_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_COOKIE_STORE", "memory")
    assert isinstance(create_cookie_store("test", cookie_store="file"), FileCookieStore)
    assert isinstance(create_cookie_store("test", cookie_store=None), MemoryCookieStore)


# ------------------------------------------------------------------ stealth session


def _mock_curl_session(prime_response=None, **kwargs):
    mock = MagicMock()
    mock.cookies = {"nsit": "abc"}
    if prime_response is not None:
        mock.get.return_value = prime_response
    return mock


def _http(status, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {"content-type": "application/json"}
    return resp


@patch("src.scrapers.session.CurlSession")
def test_prime_sets_cookie_and_ttl(mock_curl_class):
    mock_session = _mock_curl_session()
    mock_session.get.return_value = _http(200, {"content-type": "text/html"})
    mock_curl_class.return_value = mock_session

    s = StealthSession(make_config())
    assert s.prime() is True
    assert s._primed_at is not None

    # second prime within TTL does not hit the network again
    mock_session.get.reset_mock()
    assert s.prime() is True
    mock_session.get.assert_not_called()


@patch("src.scrapers.session.CurlSession")
def test_prime_failure_raises_cookie_error(mock_curl_class):
    mock_session = _mock_curl_session()
    mock_session.get.return_value = _http(403)
    mock_curl_class.return_value = mock_session

    s = StealthSession(make_config())
    with pytest.raises(CookieError):
        s.request("GET", "https://example.com/api")


@patch("src.scrapers.session.CurlSession")
def test_request_success_path(mock_curl_class):
    mock_session = _mock_curl_session()
    mock_session.get.return_value = _http(200)
    resp = _http(200)
    mock_session.request.return_value = resp
    mock_curl_class.return_value = mock_session

    s = StealthSession(make_config())
    out = s.request("GET", "https://example.com/api", referer="https://example.com/q")
    assert out is resp
    # primed first, then the api request
    mock_session.get.assert_called_once()
    mock_session.request.assert_called_once()


@patch("src.scrapers.session.CurlSession")
def test_request_429_does_not_clear_cookies(mock_curl_class):
    mock_session = _mock_curl_session()
    mock_session.get.return_value = _http(200)
    mock_session.request.return_value = _http(429)
    mock_curl_class.return_value = mock_session

    s = StealthSession(make_config())
    s.prime()  # marks primed
    with pytest.raises(SessionExhausted):
        s.request("GET", "https://example.com/api")
    # 429 only backs off; the primed cookie must still be present (D-07)
    assert mock_session.cookies.get("nsit") == "abc"


@patch("src.scrapers.session.CurlSession")
def test_request_401_reprimes_then_raises_cookie_error(mock_curl_class):
    mock_session = _mock_curl_session()
    mock_session.get.return_value = _http(200)
    mock_session.request.return_value = _http(401)
    mock_curl_class.return_value = mock_session

    s = StealthSession(make_config())
    s.prime()
    with pytest.raises(CookieError):
        s.request("GET", "https://example.com/api")
    # initial prime + re-prime after 401 + prime retry on next attempt
    assert mock_session.get.call_count == 3


@patch("src.scrapers.session.CurlSession")
def test_request_validation_failure_retries_then_exhausts(mock_curl_class):
    mock_session = _mock_curl_session()
    mock_session.get.return_value = _http(200)
    mock_session.request.return_value = _http(200, {"content-type": "text/html"})
    mock_curl_class.return_value = mock_session

    def reject_html(resp):
        if "text/html" in resp.headers.get("content-type", ""):
            raise BlockedResponse("html block page")

    s = StealthSession(make_config(retries=2))
    s.prime()
    with pytest.raises(SessionExhausted):
        s.request("GET", "https://example.com/api", validate=reject_html)
    assert mock_session.request.call_count == 2


@patch("src.scrapers.session.CurlSession")
def test_request_survives_500_then_succeeds(mock_curl_class):
    mock_session = _mock_curl_session()
    mock_session.get.return_value = _http(200)
    ok = _http(200)
    mock_session.request.side_effect = [_http(500), ok]
    mock_curl_class.return_value = mock_session

    s = StealthSession(make_config())
    s.prime()
    assert s.request("GET", "https://example.com/api") is ok
    assert mock_session.request.call_count == 2

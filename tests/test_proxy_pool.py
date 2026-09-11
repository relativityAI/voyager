"""Tests for the simple proxy pool manager (src/scrapers/proxy_pool.py)."""

from __future__ import annotations

from unittest.mock import patch

from src.scrapers.proxy_pool import ProxyPool, get_proxy_pool

# -------------------------------------------------------------- ProxyPool


def test_pool_empty_returns_none():
    pool = ProxyPool()
    assert pool.get_proxy() is None


def test_pool_returns_proxy():
    pool = ProxyPool(proxies=["http://1.2.3.4:8080", "http://5.6.7.8:3128"])
    proxy = pool.get_proxy()
    assert proxy in ("http://1.2.3.4:8080", "http://5.6.7.8:3128")


def test_pool_strips_whitespace_and_ignores_empty():
    pool = ProxyPool(
        proxies=["  http://1.2.3.4:8080  ", "", "http://5.6.7.8:3128"]
    )
    assert pool.size == 2
    proxy = pool.get_proxy()
    assert proxy in ("http://1.2.3.4:8080", "http://5.6.7.8:3128")


def test_pool_size():
    assert ProxyPool().size == 0
    assert ProxyPool(proxies=["http://1.2.3.4:8080"]).size == 1


def test_pool_mark_failed_is_noop():
    pool = ProxyPool(proxies=["http://1.2.3.4:8080"])
    pool.mark_failed("http://1.2.3.4:8080")
    assert pool.size == 1
    assert pool.get_proxy() == "http://1.2.3.4:8080"


def test_pool_mark_success_is_noop():
    pool = ProxyPool(proxies=["http://1.2.3.4:8080"])
    pool.mark_success("http://1.2.3.4:8080")
    assert pool.size == 1


def test_pool_force_refresh_is_noop():
    pool = ProxyPool(proxies=["http://1.2.3.4:8080"])
    pool.force_refresh()
    assert pool.size == 1


# -------------------------------------------------------------- get_proxy_pool env


@patch("src.scrapers.proxy_pool.ProxyPool")
def test_get_proxy_pool_reads_env(mock_pool_cls):
    with patch.dict(
        "os.environ",
        {"PROXY_POOL": "http://1.2.3.4:8080, http://5.6.7.8:3128"},
        clear=False,
    ):
        with patch("src.scrapers.proxy_pool._pool", None):
            get_proxy_pool()
    mock_pool_cls.assert_called_once_with(
        proxies=["http://1.2.3.4:8080", "http://5.6.7.8:3128"]
    )


@patch("src.scrapers.proxy_pool.ProxyPool")
def test_get_proxy_pool_defaults_empty(mock_pool_cls):
    with patch.dict("os.environ", {"PROXY_POOL": ""}, clear=False):
        with patch("src.scrapers.proxy_pool._pool", None):
            get_proxy_pool()
    mock_pool_cls.assert_called_once_with(proxies=[])


def test_get_proxy_pool_singleton():
    with patch.dict(
        "os.environ", {"PROXY_POOL": "http://1.2.3.4:8080"}, clear=False
    ):
        with patch("src.scrapers.proxy_pool._pool", None):
            pool1 = get_proxy_pool()
            pool2 = get_proxy_pool()
            assert pool1 is pool2

"""Tests for the free proxy pool manager (src/scrapers/proxy_pool.py)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from src.scrapers.proxy_pool import (
    ProxyInfo,
    ProxyPool,
    _dedupe,
    _parse_iplocate_line,
)

# -------------------------------------------------------------- ProxyInfo


def test_priority_indian_residential():
    p = ProxyInfo(ip="1.2.3.4", port=80, country_code="IN", asn=55836)
    assert p.priority == 0
    assert p.is_indian


def test_priority_indian_datacenter():
    p = ProxyInfo(ip="1.2.3.4", port=80, country_code="IN", asn=8075)
    assert p.priority == 1


def test_priority_non_datacenter_other():
    p = ProxyInfo(ip="1.2.3.4", port=80, country_code="US", asn=12345)
    assert p.priority == 2


def test_priority_datacenter():
    p = ProxyInfo(ip="1.2.3.4", port=80, country_code="US", asn=8075)
    assert p.priority == 3


def test_url_property():
    p = ProxyInfo(ip="10.0.0.1", port=3128, protocol="socks5")
    assert p.url == "socks5://10.0.0.1:3128"


# -------------------------------------------------------------- parse


def test_parse_iplocate_simple():
    p = _parse_iplocate_line("1.2.3.4:8080")
    assert p is not None
    assert p.ip == "1.2.3.4"
    assert p.port == 8080
    assert p.protocol == "http"


def test_parse_iplocate_with_protocol():
    p = _parse_iplocate_line("socks5://10.0.0.1:1080")
    assert p is not None
    assert p.protocol == "socks5"
    assert p.port == 1080


def test_parse_iplocate_blank():
    assert _parse_iplocate_line("") is None
    assert _parse_iplocate_line("# comment") is None


def test_parse_iplocate_bad_port():
    assert _parse_iplocate_line("1.2.3.4:notaport") is None


# -------------------------------------------------------------- dedupe


def test_dedup_removes_duplicates():
    a = ProxyInfo(ip="1.2.3.4", port=80)
    b = ProxyInfo(ip="1.2.3.4", port=80)
    c = ProxyInfo(ip="1.2.3.4", port=8080)
    result = _dedupe([a, b, c])
    assert len(result) == 2


# -------------------------------------------------------------- ProxyPool


def _make_pool(**kwargs) -> ProxyPool:
    return ProxyPool(fetch_ttl=300.0, **kwargs)


@patch("src.scrapers.proxy_pool._fetch_clearproxy", return_value=[])
@patch("src.scrapers.proxy_pool._fetch_iplocate", return_value=[])
def test_pool_empty_returns_none(mock_il, mock_cp):
    pool = _make_pool()
    assert pool.get_proxy() is None


@patch("src.scrapers.proxy_pool._fetch_clearproxy", return_value=[])
@patch("src.scrapers.proxy_pool._fetch_iplocate")
def test_pool_prioritizes_indian_residential(mock_il, mock_cp):
    mock_il.return_value = [
        ProxyInfo(ip="1.1.1.1", port=80, country_code="US"),  # priority 3
        ProxyInfo(ip="2.2.2.2", port=80, country_code="IN", asn=55836),  # priority 0
        ProxyInfo(ip="3.3.3.3", port=80, country_code="IN", asn=8075),  # priority 1
    ]
    pool = _make_pool()
    pool._validate = MagicMock(return_value=True)
    proxy = pool.get_proxy()
    assert proxy == "http://2.2.2.2:80"


@patch("src.scrapers.proxy_pool._fetch_clearproxy", return_value=[])
@patch("src.scrapers.proxy_pool._fetch_iplocate")
def test_pool_skips_validated_proxy(mock_il, mock_cp):
    pool = _make_pool()
    pool._proxies = [
        ProxyInfo(ip="1.1.1.1", port=80, country_code="US",
                  last_validated=time.monotonic(), fail_count=0),
        ProxyInfo(ip="2.2.2.2", port=80, country_code="IN", asn=55836),
    ]
    pool._last_fetch = time.monotonic()
    proxy = pool.get_proxy()
    assert proxy == "http://1.1.1.1:80"


def test_pool_mark_failed_increments():
    pool = _make_pool()
    pool._proxies = [ProxyInfo(ip="1.1.1.1", port=80)]
    pool.mark_failed("http://1.1.1.1:80")
    assert pool._proxies[0].fail_count == 1


def test_pool_mark_success_sets_validated():
    pool = _make_pool()
    pool._proxies = [ProxyInfo(ip="1.1.1.1", port=80, fail_count=3)]
    pool.mark_success("http://1.1.1.1:80")
    assert pool._proxies[0].fail_count == 0
    assert pool._proxies[0].last_validated > 0


def test_pool_force_refresh():
    pool = _make_pool()
    pool._last_fetch = 100.0
    with patch("src.scrapers.proxy_pool._fetch_clearproxy", return_value=[]), \
         patch("src.scrapers.proxy_pool._fetch_iplocate", return_value=[]):
        pool.force_refresh()
    assert pool._last_fetch != 100.0


@patch("src.scrapers.proxy_pool._fetch_clearproxy", return_value=[])
@patch("src.scrapers.proxy_pool._fetch_iplocate")
def test_pool_skips_failed_proxies(mock_il, mock_cp):
    mock_il.return_value = [
        ProxyInfo(ip="1.1.1.1", port=80, fail_count=3),
        ProxyInfo(ip="2.2.2.2", port=80),
    ]
    pool = _make_pool()
    pool._ensure_fresh()
    pool._last_fetch = time.monotonic()
    pool._validate = MagicMock(return_value=True)
    proxy = pool.get_proxy()
    assert proxy == "http://2.2.2.2:80"


@patch("src.scrapers.proxy_pool._fetch_clearproxy", return_value=[])
@patch("src.scrapers.proxy_pool._fetch_iplocate")
def test_pool_validates_before_returning(mock_il, mock_cp):
    mock_il.return_value = [ProxyInfo(ip="1.1.1.1", port=80)]
    pool = _make_pool()
    pool._validate = MagicMock(return_value=True)
    proxy = pool.get_proxy()
    assert proxy == "http://1.1.1.1:80"
    pool._validate.assert_called_once()

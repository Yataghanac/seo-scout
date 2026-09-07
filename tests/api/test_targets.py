"""Which URLs a browser client may ask this server to fetch."""

from __future__ import annotations

import pytest

from seo_scout.api.targets import blocked_ip, check_url


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",  # the cloud metadata endpoint
        "0.0.0.0",
        "::1",
        "fd00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
    ],
)
def test_internal_addresses_are_refused(ip: str) -> None:
    assert blocked_ip(ip) is not None


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert blocked_ip(ip) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://intranet/",
        "http://box.local/",
        "http://127.0.0.1:8099/",
        "http://[::1]/",
    ],
)
def test_internal_urls_are_refused(url: str) -> None:
    assert check_url(url) is not None


def test_an_ordinary_public_url_is_allowed() -> None:
    assert check_url("https://example.com/a/b") is None


def test_the_reason_names_the_host() -> None:
    reason = check_url("http://box.local/")
    assert reason is not None and "box.local" in reason


def test_an_allowlist_refuses_everything_else() -> None:
    assert check_url("https://example.com/", ["client.com"]) is not None
    assert check_url("https://client.com/", ["client.com"]) is None


def test_an_allowlist_covers_subdomains_of_the_listed_domain() -> None:
    assert check_url("https://blog.client.com/", ["client.com"]) is None


def test_an_allowlist_does_not_widen_the_address_rules() -> None:
    """The allowlist says which sites, never which addresses may be reached."""
    assert check_url("http://127.0.0.1/", ["127.0.0.1"]) is not None

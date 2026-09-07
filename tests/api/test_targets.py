"""Which URLs a browser client may ask this server to fetch."""

from __future__ import annotations

import socket
from typing import Any

import pytest

from seo_scout.api import targets
from seo_scout.api.targets import blocked_ip, check_url, ok


@pytest.fixture(autouse=True)
def _clear_resolve_reason_cache() -> None:
    """`resolve_reason` is `lru_cache`d; a stale hit from an earlier test must never leak in."""
    targets.resolve_reason.cache_clear()


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


def test_blocked_ip_refuses_a_value_that_is_not_an_ip_address() -> None:
    assert blocked_ip("not-an-ip") is not None


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


@pytest.mark.parametrize("url", ["", "not a url", "/relative/path"])
def test_a_url_with_no_host_is_refused(url: str) -> None:
    """`check_url` is the entry point for free text a client pastes in; a client can paste
    anything, including text that never parses into a host at all."""
    reason = check_url(url)
    assert reason is not None and "no host" in reason


def test_the_reason_names_the_host() -> None:
    reason = check_url("http://box.local/")
    assert reason is not None and "box.local" in reason


def test_an_allowlist_narrows_which_sites_are_allowed() -> None:
    assert check_url("https://example.com/", ["client.com"]) is not None
    assert check_url("https://client.com/", ["client.com"]) is None


def test_an_allowlist_covers_subdomains_of_the_listed_domain() -> None:
    assert check_url("https://blog.client.com/", ["client.com"]) is None


def test_an_allowlist_does_not_widen_the_address_rules() -> None:
    """The allowlist says which sites, never which addresses may be reached."""
    assert check_url("http://127.0.0.1/", ["127.0.0.1"]) is not None


class _FakeResolver:
    """Stands in for `socket.getaddrinfo`: never touches the network.

    Returns 5-tuples shaped like the real call `(family, type, proto, canonname, sockaddr)`
    with the address at `info[4][0]`, which is what `resolve_reason` reads. Counts calls so
    tests can prove the `lru_cache` (or `ok`'s short-circuit) actually did its job.
    """

    def __init__(self, *addresses: str, error: OSError | None = None) -> None:
        self.addresses = addresses
        self.error = error
        self.calls = 0

    def __call__(self, *_args: Any, **_kwargs: Any) -> list[tuple[int, int, int, str, Any]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in self.addresses]


def test_a_host_with_any_internal_address_is_refused_even_with_a_public_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host can publish both a public and a private record; every address is checked."""
    fake = _FakeResolver("93.184.216.34", "10.0.0.5")
    monkeypatch.setattr(targets.socket, "getaddrinfo", fake)
    assert targets.resolve_reason("mixed.example") is not None


def test_a_purely_public_host_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeResolver("93.184.216.34")
    monkeypatch.setattr(targets.socket, "getaddrinfo", fake)
    assert targets.resolve_reason("public-only.example") is None


def test_a_host_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeResolver(error=socket.gaierror("nodename nor servname provided"))
    monkeypatch.setattr(targets.socket, "getaddrinfo", fake)
    reason = targets.resolve_reason("nowhere.example")
    assert reason is not None and "does not resolve" in reason


def test_resolve_reason_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """`allowed` runs for every link a crawl enqueues; a DNS lookup per host must run once."""
    fake = _FakeResolver("93.184.216.34")
    monkeypatch.setattr(targets.socket, "getaddrinfo", fake)
    targets.resolve_reason("cached.example")
    targets.resolve_reason("cached.example")
    assert fake.calls == 1
    info = targets.resolve_reason.cache_info()
    assert (info.hits, info.misses) == (1, 1)


def test_ok_refuses_a_bad_url_without_ever_resolving_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """`check_url` is the cheap gate; `ok` must not pay for a DNS lookup it doesn't need."""
    fake = _FakeResolver("93.184.216.34")  # would make the host look fine, if it ran at all
    monkeypatch.setattr(targets.socket, "getaddrinfo", fake)
    assert ok("http://127.0.0.1/") is False
    assert fake.calls == 0


def test_ok_allows_a_public_url_that_resolves_publicly(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeResolver("93.184.216.34")
    monkeypatch.setattr(targets.socket, "getaddrinfo", fake)
    assert ok("https://public.example/") is True


def test_ok_respects_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """`check_url`'s allowlist check refuses before `ok` ever reaches DNS resolution."""
    fake = _FakeResolver("93.184.216.34")  # would make the host look fine, if it ran at all
    monkeypatch.setattr(targets.socket, "getaddrinfo", fake)
    assert ok("https://outside-allowlist.example/", ["client.com"]) is False
    assert fake.calls == 0

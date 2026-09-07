"""PinningTransport: resolve once, connect to exactly that address.

respx mocks httpx at the transport layer, which is the very thing under test here, so it
cannot be used. Instead `httpx.AsyncHTTPTransport.handle_async_request` (what `super()` calls)
is stubbed to record the request it was handed and hand back a canned response — no socket is
ever opened — and `asyncio`'s event loop resolver is stubbed the same way, so DNS answers are
scripted per test.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from seo_scout.api.targets import blocked_ip
from seo_scout.crawler.pinning import PinningTransport

AddrInfo = tuple[int, int, int, str, tuple[str, int]]


def addrinfo(address: str) -> AddrInfo:
    """One `socket.getaddrinfo` result carrying `address`, shaped like the real thing."""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (address, 0))


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Stub the transport's inherited handler: record the request, never touch a socket."""
    rec = Recorder()

    async def fake_handle(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_handle)
    return rec


def fake_getaddrinfo(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, list[AddrInfo]]
) -> list[str]:
    """Script the event loop's resolver. Returns the list of hosts it was asked to resolve."""
    calls: list[str] = []

    async def fake(
        self: asyncio.AbstractEventLoop, host: str, port: object, *args: object, **kw: object
    ) -> list[AddrInfo]:
        calls.append(host)
        return answers[host]

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "getaddrinfo", fake)
    return calls


def allow(_: str) -> str | None:
    return None


async def test_a_publicly_resolving_host_is_pinned(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    fake_getaddrinfo(monkeypatch, {"example.com": [addrinfo("93.184.216.34")]})
    transport = PinningTransport(blocked=allow)
    request = httpx.Request("GET", "https://example.com/path")

    response = await transport.handle_async_request(request)

    assert response.status_code == 200
    sent = recorder.requests[0]
    assert sent.url.host == "93.184.216.34"
    assert sent.headers["host"] == "example.com"
    assert sent.extensions["sni_hostname"] == "example.com"


async def test_a_host_resolving_internally_is_refused_before_connecting(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    fake_getaddrinfo(monkeypatch, {"internal.example": [addrinfo("10.0.0.5")]})
    transport = PinningTransport(blocked=blocked_ip)
    request = httpx.Request("GET", "https://internal.example/")

    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(request)

    assert recorder.requests == []  # the inherited handler was never reached


async def test_an_ipv6_address_is_bracketed_in_the_pinned_url(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    fake_getaddrinfo(monkeypatch, {"example.com": [addrinfo("2001:db8::1")]})
    transport = PinningTransport(blocked=allow)
    request = httpx.Request("GET", "https://example.com/")

    await transport.handle_async_request(request)

    assert "[2001:db8::1]" in str(recorder.requests[0].url)


async def test_an_ip_literal_url_is_checked_without_a_lookup(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    calls = fake_getaddrinfo(monkeypatch, {})  # any lookup here is a bug, so no answers exist
    transport = PinningTransport(blocked=allow)
    request = httpx.Request("GET", "https://203.0.113.5/x")

    await transport.handle_async_request(request)

    assert calls == []
    assert recorder.requests[0].url.host == "203.0.113.5"


async def test_an_ip_literal_url_is_still_refused_when_blocked(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    fake_getaddrinfo(monkeypatch, {})
    transport = PinningTransport(blocked=blocked_ip)
    request = httpx.Request("GET", "http://127.0.0.1/x")

    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(request)

    assert recorder.requests == []


async def test_no_blocked_predicate_leaves_the_request_completely_untouched(
    recorder: Recorder,
) -> None:
    transport = PinningTransport(blocked=None)
    request = httpx.Request("GET", "https://example.com/path")

    await transport.handle_async_request(request)

    assert recorder.requests[0] is request  # not even copied, let alone rewritten


async def test_rebinding_is_refused_even_though_the_dashboard_preflight_check_passed(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    """The scenario this module exists for.

    `api.targets` already checked this host once and let it through, because at that
    moment it resolved to a public address. Nothing keeps DNS from answering differently a
    moment later, at the point the crawler actually connects — which is exactly what the
    transport's own, independent resolution below simulates. The transport's decision is
    what a request that reaches this attacker's host must pass, not the earlier one.
    """
    assert blocked_ip("93.184.216.34") is None  # the dashboard's own check, moments earlier

    fake_getaddrinfo(monkeypatch, {"rebinder.example": [addrinfo("169.254.169.254")]})
    transport = PinningTransport(blocked=blocked_ip)
    request = httpx.Request("GET", "https://rebinder.example/steal")

    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(request)

    assert recorder.requests == []

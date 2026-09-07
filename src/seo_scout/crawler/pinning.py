"""Pin a request to the address it was checked against, so the connection cannot re-resolve.

`api/targets.py` decides whether a client-supplied URL is safe to crawl, but that decision and
the connection httpx eventually makes are two separate DNS lookups: a host that answers with a
public address when checked and an internal one a moment later is fetched anyway. That gap is
DNS rebinding. This module closes it the only way that actually works — resolve the host once,
refuse it here if it is unsafe, and hand httpcore the resolved address directly — while keeping
TLS validation and the `Host` header on the original hostname via `sni_hostname`.

This module must not import the dashboard's target-safety package: the refusal predicate is
injected by the caller instead, so `crawler/` stays independent of `api/` (see CLAUDE.md's
layering rule). `PinningTransport` also does nothing at all when `blocked` is `None`, which is
what keeps the CLI's crawl path (and `dev/fixture_site.py`, crawled over `127.0.0.1`)
byte-for-byte unchanged.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Callable
from typing import Any

import httpx

Blocked = Callable[[str], str | None]


class PinningTransport(httpx.AsyncHTTPTransport):
    """An `AsyncHTTPTransport` that connects to exactly the address it resolved and checked.

    `blocked(ip) -> reason | None` is asked about every address this transport would
    otherwise hand to httpcore. When it is `None` (the default), the transport is inert:
    every request goes to `super()` untouched, resolution included.
    """

    def __init__(self, *, blocked: Blocked | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._blocked = blocked

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._blocked is None:
            return await super().handle_async_request(request)
        host = request.url.host
        if _literal_address(host) is not None:
            # Already an address: checked, but there is no name to preserve or resolve.
            if reason := self._blocked(host):
                raise httpx.ConnectError(reason)
            return await super().handle_async_request(request)
        addr = await _resolve_one(host, self._blocked)
        original_host_header = request.headers.get("host", host)
        literal = f"[{addr}]" if ":" in addr else addr
        request.url = request.url.copy_with(host=literal)
        request.headers["host"] = original_host_header
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await super().handle_async_request(request)


def _literal_address(host: str) -> str | None:
    """`host` itself, if it is already an IP literal (brackets stripped for IPv6)."""
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


async def _resolve_one(host: str, blocked: Blocked) -> str:
    """Resolve `host` off the event loop, refusing it if any address `blocked` flags.

    Every address is checked, not just the first the resolver returns — a host that
    publishes one public and one internal record cannot pick the record it is checked
    against by which one is queried first, since only the checked address is ever used.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except OSError as exc:
        raise httpx.ConnectError(f"{host} does not resolve") from exc
    # sockaddr is (address, port) for IPv4, (address, port, flow, scope) for IPv6, or (a
    # protocol-family-specific pair typed str|int by the stub); a stream host resolution
    # only ever produces the first two, so the type guard also drops nothing real.
    addresses = [str(info[4][0]) for info in infos if isinstance(info[4][0], str)]
    for addr in addresses:
        if reason := blocked(addr):
            raise httpx.ConnectError(reason)
    if not addresses:
        raise httpx.ConnectError(f"{host} does not resolve")
    return addresses[0]

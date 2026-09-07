"""Which URLs a browser client may ask this server to fetch.

The dashboard lets a stranger type a URL that this process then requests, which is a
server-side request forgery hole unless something says no. `check_url` and `blocked_ip` are
pure; `resolve_reason` is the one function here that does I/O, and it is cached per host
because `allowed` is consulted for every link a crawl enqueues, not only for redirect hops.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence
from functools import lru_cache
from urllib.parse import urlsplit

from seo_scout.urls import registrable_domain

_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa")


def blocked_ip(value: str) -> str | None:
    """A refusal reason for an address a public crawl has no business reaching, else None."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return f"{value} is not an IP address"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return blocked_ip(str(ip.ipv4_mapped))  # ::ffff:127.0.0.1 is loopback
    checks = (
        (ip.is_loopback, "loopback"),
        (ip.is_link_local, "link-local"),
        (ip.is_private, "private"),
        (ip.is_reserved or ip.is_multicast or ip.is_unspecified, "reserved"),
    )
    for is_match, label in checks:
        if is_match:
            return f"{value} is a {label} address"
    return None


def _hostname_reason(host: str, url: str, allowlist: Sequence[str]) -> str | None:
    """A refusal reason for a non-IP host, else None. Split out of `check_url` for PLR0911."""
    if host.endswith(_LOCAL_SUFFIXES):
        return f"{host} is a local network name"
    if "." not in host:
        return f"{host} is a bare hostname, not a public domain"
    if not allowlist:
        return None
    domain = registrable_domain(url)
    if domain.lower() not in {d.strip().lower() for d in allowlist}:
        return f"{domain or host} is not one of this deployment's allowed sites"
    return None


def _host(url: str) -> str:
    """The lowercased hostname component of `url`, or "" if it has none."""
    return (urlsplit(url).hostname or "").strip().lower()


def check_url(url: str, allowlist: Sequence[str] = ()) -> str | None:
    """A refusal reason for a URL a client may not ask us to crawl, else None.

    No DNS here: this is the cheap, pure gate. `resolve_reason` does the lookup. Scheme is
    not checked here either: the endpoint calls `urls.link_pair` first, which rejects
    anything that isn't http(s), so this function alone does not make a URL safe to fetch.
    """
    host = _host(url)
    if not host:
        return "that URL has no host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return _hostname_reason(host, url, allowlist)
    if allowlist:
        return f"{host} is an address, not one of this deployment's allowed sites"
    return blocked_ip(host)


@lru_cache(maxsize=512)
def resolve_reason(host: str) -> str | None:
    """A refusal reason if any address `host` resolves to is internal, else None.

    Every address is checked, not just the first: a host can publish one public and one
    private record. A host that does not resolve is refused rather than left to the crawler.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return f"{host} does not resolve"
    for info in infos:
        if reason := blocked_ip(str(info[4][0])):
            return reason
    return None


def ok(url: str, allowlist: Sequence[str] = ()) -> bool:
    """The whole gate as one predicate, for composing into the crawler's `allowed` hook."""
    if check_url(url, allowlist) is not None:
        return False
    return resolve_reason(_host(url)) is None

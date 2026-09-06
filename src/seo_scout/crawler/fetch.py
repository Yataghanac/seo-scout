"""Single-URL fetcher: redirects recorded by hand, backoff, size and type gates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel

from seo_scout.models import RedirectHop
from seo_scout.urls import looks_binary, resolve, same_site

log = logging.getLogger("seo_scout.crawler.fetch")

SleepFn = Callable[[float], Awaitable[None]]
Allowed = Callable[[str], bool]

# What httpx raises when asked to build a request the stdlib parser accepted: a bad IPv4
# literal or an over-long URL (InvalidURL), a malformed punycode label (idna's errors are
# UnicodeErrors). Discovery catches the same set around robots.txt and sitemap fetches.
BAD_URL_ERRORS = (httpx.InvalidURL, UnicodeError)
# What one `client.get` of untrusted text (a robots.txt line, a sitemap entry) can raise.
UNFETCHABLE = (httpx.HTTPError, *BAD_URL_ERRORS)

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_REDIRECTS = {301, 302, 303, 307, 308}
_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"
MAX_TRIES = 3
MAX_REDIRECTS = 10

# httpx builds the next request from a Location header even with follow_redirects=False
# and raises (InvalidURL, or RemoteProtocolError for a malformed http URL) when it cannot;
# the response is closed and gone by then. Response hooks run before that build, so this
# one keeps the response where `_request` can find it. Task-local: crawls run concurrently.
_last_response: ContextVar[httpx.Response | None] = ContextVar("seo_scout_last", default=None)


async def _remember(response: httpx.Response) -> None:
    _last_response.set(response)


class NetworkError(Exception):
    """The request never completed after all retries (DNS, connect, timeout)."""


class FetchResult(BaseModel):
    url: str
    final_url: str
    status: int
    content_type: str | None
    body: str | None
    bytes: int
    elapsed_ms: int
    redirect_chain: list[RedirectHop] = []
    headers: dict[str, str] = {}
    # non_html | too_large | bad_url (httpx refused to build the request; status 0) |
    # too_many_redirects (10 distinct hops) | redirect_loop (a hop already in this chain) |
    # off_site_redirect | bad_redirect (Location is not an http(s) URL) |
    # disallowed_redirect (the `allowed` gate refused a hop)
    skipped: str | None = None


@dataclass
class _Attempt:
    status: int
    content_type: str | None
    headers: dict[str, str]
    body: str | None = None
    size: int = 0
    skipped: str | None = None


def _content_type(raw: str | None) -> str | None:
    return raw.split(";", 1)[0].strip().lower() or None if raw else None


def _charset(raw: str | None, default: str = "utf-8") -> str:
    if raw and "charset=" in raw:
        return raw.split("charset=", 1)[1].split(";", 1)[0].strip(" \"'") or default
    return default


def _retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _attempt_of(response: httpx.Response) -> _Attempt:
    """Status and headers of a response whose body has not been read."""
    headers = {k.lower(): v for k, v in response.headers.items()}
    ctype = _content_type(headers.get("content-type"))
    return _Attempt(status=response.status_code, content_type=ctype, headers=headers)


def _redirect_target(current: str, location: str) -> str | None:
    """Wire form of a Location header, or None when no crawler could follow it.

    urljoin first, then resolve: a fragment-only Location (`#top`) resolved on its own is
    a skipped anchor, joined to the current URL it is the same page (a loop).
    """
    try:
        return resolve(urljoin(current, location))
    except ValueError:  # urljoin parses too, and `https://[::1/x` fails there
        return None


class Fetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        max_bytes: int,
        timeout: float,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._client = client
        self._headers = {"user-agent": user_agent, "accept": _ACCEPT}
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._sleep = sleep
        hooks = client.event_hooks["response"]
        if _remember not in hooks:
            hooks.append(_remember)

    async def fetch(self, url: str, *, allowed: Allowed | None = None) -> FetchResult:
        """Fetch one URL, following same-site redirects and recording every hop.

        `allowed` (robots.txt in the crawler) is asked before every hop: a permitted `/go`
        that redirects into `/private/` must not fetch it.
        """
        started = perf_counter()
        chain: list[RedirectHop] = []
        current = url
        visited = {resolve(url) or url}  # wire forms of every URL requested so far
        attempt = _Attempt(status=0, content_type=None, headers={})
        for _ in range(MAX_REDIRECTS):
            attempt = await self._fetch_once(current)
            location = attempt.headers.get("location")
            if attempt.status not in _REDIRECTS or not location:
                return self._result(url, current, attempt, chain, started)
            chain.append(RedirectHop(url=current, status=attempt.status))
            # Request the path the server named (normalize() would collapse /a/ back to
            # /a and loop), but compare on the wire form: host case, a default port or a
            # fragment never change the request, so such a Location, or any URL already
            # in this chain (A->B->A), is a loop and stops here rather than after ten hops.
            target = _redirect_target(current, location)
            if target is None:
                attempt.skipped = "bad_redirect"
                return self._result(url, current, attempt, chain, started)
            if not same_site(url, target):
                attempt.skipped = "off_site_redirect"
                return self._result(url, target, attempt, chain, started)
            if target in visited:
                attempt.skipped = "redirect_loop"
                return self._result(url, current, attempt, chain, started)
            if allowed is not None and not allowed(target):
                attempt.skipped = "disallowed_redirect"
                return self._result(url, target, attempt, chain, started)
            visited.add(target)
            current = target
        attempt.skipped = "too_many_redirects"
        return self._result(url, current, attempt, chain, started)

    async def _fetch_once(self, url: str) -> _Attempt:
        """HEAD first for binary-looking URLs so we never download a PDF to discover it is one."""
        if looks_binary(url):
            head = await self._with_retry("HEAD", url)
            if head.skipped or (head.status < 300 and head.content_type not in _HTML_TYPES):
                head.skipped = head.skipped or "non_html"
                return head
        return await self._with_retry("GET", url)

    async def _with_retry(self, method: str, url: str) -> _Attempt:
        """Exponential backoff on 429/5xx/transport errors; honours Retry-After; 3 tries."""
        delay = 0.5
        for try_no in range(1, MAX_TRIES + 1):
            try:
                attempt = await self._request(method, url)
            except httpx.TransportError as exc:
                if try_no == MAX_TRIES:
                    raise NetworkError(f"{method} {url}: {exc!r}") from exc
                log.debug("transport error, retrying", extra={"url": url, "try": try_no})
                await self._sleep(delay)
                delay *= 2
                continue
            if (attempt.status != 429 and attempt.status < 500) or try_no == MAX_TRIES:
                return attempt
            wait = _retry_after(attempt.headers) or delay
            log.debug("backing off", extra={"url": url, "status": attempt.status, "wait": wait})
            await self._sleep(wait)
            delay *= 2
        raise AssertionError("unreachable")

    async def _request(self, method: str, url: str) -> _Attempt:
        """One request. A URL httpx will not build is `bad_url`; a Location it cannot build
        the next request from hands the response back for `fetch()` to classify."""
        try:
            request = self._client.build_request(
                method, url, headers=self._headers, timeout=self._timeout
            )
        except BAD_URL_ERRORS as exc:
            log.debug("httpx refused the URL", extra={"url": url, "error": str(exc)})
            return _Attempt(status=0, content_type=None, headers={}, skipped="bad_url")
        _last_response.set(None)
        try:
            return await self._stream(request)
        except (httpx.InvalidURL, httpx.RemoteProtocolError):
            response = _last_response.get()
            if response is None or not response.has_redirect_location:
                raise  # a real protocol error, retried like any transport error
            return _attempt_of(response)

    async def _stream(self, request: httpx.Request) -> _Attempt:
        """Bodies are only read for 2xx HTML within the size cap."""
        response = await self._client.send(request, stream=True, follow_redirects=False)
        try:
            attempt = _attempt_of(response)
            if request.method == "HEAD" or attempt.status >= 300:
                return attempt
            if attempt.content_type is not None and attempt.content_type not in _HTML_TYPES:
                attempt.skipped = "non_html"
                return attempt
            declared = attempt.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self._max_bytes:
                attempt.skipped = "too_large"
                return attempt
            buf = bytearray()
            async for chunk in response.aiter_bytes():
                buf += chunk
                if len(buf) > self._max_bytes:
                    attempt.skipped = "too_large"
                    return attempt
            attempt.size = len(buf)
            charset = _charset(attempt.headers.get("content-type"))
            attempt.body = buf.decode(charset, errors="replace")
            return attempt
        finally:
            await response.aclose()

    @staticmethod
    def _result(
        url: str, final_url: str, attempt: _Attempt, chain: list[RedirectHop], started: float
    ) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=final_url,
            status=attempt.status,
            content_type=attempt.content_type,
            body=attempt.body,
            bytes=attempt.size,
            elapsed_ms=int((perf_counter() - started) * 1000),
            redirect_chain=chain,
            headers=attempt.headers,
            skipped=attempt.skipped,
        )

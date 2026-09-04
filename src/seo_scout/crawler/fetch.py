"""Single-URL fetcher: redirects recorded by hand, backoff, size and type gates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

import httpx
from pydantic import BaseModel

from seo_scout.models import RedirectHop
from seo_scout.urls import looks_binary, normalize, same_site

log = logging.getLogger("seo_scout.crawler.fetch")

SleepFn = Callable[[float], Awaitable[None]]

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_REDIRECTS = {301, 302, 303, 307, 308}
_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"
MAX_TRIES = 3
MAX_REDIRECTS = 10


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
    skipped: str | None = None  # non_html | too_large | too_many_redirects | off_site_redirect


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

    async def fetch(self, url: str) -> FetchResult:
        """Fetch one URL, following same-site redirects and recording every hop."""
        started = perf_counter()
        chain: list[RedirectHop] = []
        current = url
        attempt = _Attempt(status=0, content_type=None, headers={})
        for _ in range(MAX_REDIRECTS):
            attempt = await self._fetch_once(current)
            location = attempt.headers.get("location")
            if attempt.status not in _REDIRECTS or not location:
                return self._result(url, current, attempt, chain, started)
            chain.append(RedirectHop(url=current, status=attempt.status))
            target = normalize(location, current) or location
            if not same_site(url, target):
                attempt.skipped = "off_site_redirect"
                return self._result(url, target, attempt, chain, started)
            current = target
        attempt.skipped = "too_many_redirects"
        return self._result(url, current, attempt, chain, started)

    async def _fetch_once(self, url: str) -> _Attempt:
        """HEAD first for binary-looking URLs so we never download a PDF to discover it is one."""
        if looks_binary(url):
            head = await self._with_retry("HEAD", url)
            if head.status < 300 and head.content_type not in _HTML_TYPES:
                head.skipped = "non_html"
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
        """One streamed request. Bodies are only read for 2xx HTML within the size cap."""
        async with self._client.stream(
            method, url, headers=self._headers, timeout=self._timeout, follow_redirects=False
        ) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            ctype = _content_type(headers.get("content-type"))
            attempt = _Attempt(status=response.status_code, content_type=ctype, headers=headers)
            if method == "HEAD" or response.status_code >= 300:
                return attempt
            if ctype is not None and ctype not in _HTML_TYPES:
                attempt.skipped = "non_html"
                return attempt
            declared = headers.get("content-length")
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
            attempt.body = buf.decode(_charset(headers.get("content-type")), errors="replace")
            return attempt

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

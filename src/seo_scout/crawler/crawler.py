"""Async BFS crawler. Persists every page as soon as it is fetched."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from sqlite3 import Connection

import httpx
from pydantic import BaseModel

from seo_scout.config import Settings
from seo_scout.crawler.fetch import Fetcher, FetchResult, NetworkError, SleepFn
from seo_scout.crawler.frontier import Frontier
from seo_scout.crawler.robots import RobotsPolicy, fetch_robots
from seo_scout.crawler.sitemap import Seeds, discover_seeds
from seo_scout.logging import bind_run_id
from seo_scout.models import FetchedPage
from seo_scout.parse import parse_html
from seo_scout.store import repo_pages, repo_runs
from seo_scout.urls import Link, normalize, same_site

log = logging.getLogger("seo_scout.crawler")

MAX_CONSECUTIVE_NETWORK_FAILURES = 3


class CrawlReport(BaseModel):
    run_id: int
    status: str
    pages: int
    elapsed_s: float
    error: str | None = None


class CrawlAborted(Exception):
    """The network is gone: too many consecutive failures."""


class RateLimiter:
    """Spaces request start times at least `delay` seconds apart per crawl (one host)."""

    def __init__(self, delay: float, sleep: SleepFn) -> None:
        self._delay = delay
        self._sleep = sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            pause = self._next - now
            if pause > 0:
                await self._sleep(pause)
            self._next = max(now, self._next) + self._delay


@dataclass
class _State:
    run_id: int
    home: str
    max_pages: int
    frontier: Frontier
    policy: RobotsPolicy
    limiter: RateLimiter
    allowed: Callable[[str], bool]
    scheduled: int = 0
    fetched: int = 0
    consecutive_failures: int = 0
    pending: set[asyncio.Task[None]] = field(default_factory=set)


class Crawler:
    def __init__(
        self,
        *,
        settings: Settings,
        conn: Connection,
        client: httpx.AsyncClient,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._conn = conn
        self._client = client
        self._sleep = sleep
        self._fetcher = Fetcher(
            client,
            user_agent=settings.user_agent,
            max_bytes=settings.max_response_bytes,
            timeout=settings.request_timeout,
            sleep=sleep,
        )
        # A second gate beside robots.txt, set by whoever built this crawler. The API sets it
        # so a client-supplied URL cannot redirect into the network; the CLI leaves it open.
        self.target_ok: Callable[[str], bool] = lambda _url: True

    def _gate(self, policy: RobotsPolicy) -> Callable[[str], bool]:
        """robots.txt composed with `target_ok`. Every call site must use this, never
        `policy.allowed` directly, or a client-supplied URL could redirect into the network."""
        return lambda url: policy.allowed(url) and self.target_ok(url)

    async def plan(self, start_url: str) -> list[str]:
        """Dry run: the seed URLs a crawl would start from. Fetches only robots and sitemaps."""
        self._home(start_url)  # rejects non-http(s) input before any request
        policy, seeds = await self._discover(start_url)
        gate = self._gate(policy)
        return [u for u in seeds.urls if gate(u)]

    async def run(
        self,
        start_url: str,
        *,
        max_pages: int | None = None,
        max_depth: int | None = None,
        wall_clock_seconds: float | None = None,
    ) -> CrawlReport:
        """Crawl and persist. Caps are min(flag, configured ceiling), never higher."""
        s = self._settings
        home = self._home(start_url)
        pages_cap = min(max_pages or s.max_pages, s.max_pages)
        depth_cap = min(max_depth if max_depth is not None else s.max_depth, s.max_depth)
        budget = min(wall_clock_seconds or s.wall_clock_seconds, s.wall_clock_seconds)
        limits = {"max_pages": pages_cap, "max_depth": depth_cap, "wall_clock_seconds": budget}
        run_id = repo_runs.create_run(self._conn, home, limits)
        bind_run_id(run_id)
        log.info("crawl started", extra={"start_url": home, **limits})
        try:
            return await self._crawl(
                run_id, start_url, home=home, pages=pages_cap, depth=depth_cap, budget=budget
            )
        except Exception as exc:
            # A bug, or a library error nothing anticipated: the row must not stay
            # `running`, or every later pass and diff of this site would build on it.
            why = f"{type(exc).__name__}: {exc}"
            fetched = repo_pages.count_pages(self._conn, run_id)
            repo_runs.finish_run(self._conn, run_id, "failed", pages=fetched, error=why)
            log.error("crawl crashed", extra={"error": why})
            raise

    async def _crawl(
        self, run_id: int, start_url: str, *, home: str, pages: int, depth: int, budget: float
    ) -> CrawlReport:
        s = self._settings
        started = time.monotonic()
        policy, seeds = await self._discover(start_url)  # verbatim: the seed is fetched as given
        if policy.disallow_all:
            why = "robots.txt unreachable or 5xx; refusing to crawl (see crawling policy)"
            repo_runs.finish_run(self._conn, run_id, "failed", pages=0, error=why)
            log.error("crawl failed", extra={"error": why})
            return CrawlReport(run_id=run_id, status="failed", pages=0, elapsed_s=0, error=why)
        repo_pages.insert_sitemap_urls(self._conn, run_id, seeds.from_sitemap)
        delay = max(s.min_delay, policy.crawl_delay or 0.0)
        state = _State(
            run_id=run_id,
            home=home,
            max_pages=pages,
            frontier=Frontier(max_depth=depth),
            policy=policy,
            limiter=RateLimiter(delay, self._sleep),
            allowed=self._gate(policy),
        )
        for seed in seeds.pairs:
            self._enqueue(state, seed, 0)
        status, error = await self._run_guarded(state, budget)
        repo_runs.finish_run(self._conn, run_id, status, pages=state.fetched, error=error)
        elapsed = time.monotonic() - started
        log.info("crawl finished", extra={"status": status, "pages": state.fetched, "error": error})
        return CrawlReport(
            run_id=run_id, status=status, pages=state.fetched, elapsed_s=elapsed, error=error
        )

    async def _run_guarded(self, state: _State, budget: float) -> tuple[str, str | None]:
        try:
            async with asyncio.timeout(budget):
                await self._loop(state)
        except TimeoutError:
            return "partial", f"wall-clock budget of {budget}s exhausted"
        except CrawlAborted as exc:
            return "partial", str(exc)
        except asyncio.CancelledError:
            repo_runs.finish_run(
                self._conn, state.run_id, "partial", pages=state.fetched, error="interrupted"
            )
            raise
        return "complete", None

    async def _loop(self, state: _State) -> None:
        """Keep up to max_concurrency fetches in flight until the frontier drains."""
        concurrency = self._settings.max_concurrency
        try:
            while True:
                while state.frontier and len(state.pending) < concurrency:
                    if state.scheduled >= state.max_pages:
                        break
                    item = state.frontier.pop()
                    if item is None:
                        break
                    state.scheduled += 1
                    state.pending.add(asyncio.create_task(self._process(state, *item)))
                if not state.pending:
                    return
                done, state.pending = await asyncio.wait(
                    state.pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
        finally:
            for task in state.pending:
                task.cancel()
            await asyncio.gather(*state.pending, return_exceptions=True)

    async def _process(self, state: _State, link: Link, depth: int) -> None:
        await state.limiter.wait()
        try:
            result = await self._fetcher.fetch(link.url, allowed=state.allowed)
        except NetworkError as exc:
            self._record_failure(state, link, depth, str(exc))
            return
        state.consecutive_failures = 0
        state.frontier.mark_seen(normalize(result.final_url) or result.final_url)
        repo_pages.insert_page(self._conn, state.run_id, _to_page(result, depth, link.key))
        state.fetched += 1
        log.debug(
            "fetched", extra={"url": link.key, "status": result.status, "skipped": result.skipped}
        )
        if result.body is None:
            return
        parsed = parse_html(result.body, result.final_url)  # one Link per target page
        internal = [found for found in parsed.links if same_site(state.home, found.key)]
        repo_pages.insert_links(self._conn, state.run_id, link.key, [f.key for f in internal])
        for found in internal:
            self._enqueue(state, found, depth + 1)

    def _record_failure(self, state: _State, link: Link, depth: int, error: str) -> None:
        """The spelling that failed is stored as `final_url`, like any other row."""
        log.warning("fetch failed", extra={"url": link.key, "error": error})
        page = FetchedPage(
            url=link.key,
            final_url=link.url,
            status=0,
            depth=depth,
            content_type=None,
            bytes=0,
            elapsed_ms=0,
            fetched_at=datetime.now(UTC),
            html=None,
            error=error,
        )
        repo_pages.insert_page(self._conn, state.run_id, page)
        state.fetched += 1
        state.consecutive_failures += 1
        if state.consecutive_failures >= MAX_CONSECUTIVE_NETWORK_FAILURES:
            raise CrawlAborted(
                f"{state.consecutive_failures} consecutive network failures, last: {error}"
            )

    @staticmethod
    def _enqueue(state: _State, link: Link, depth: int) -> None:
        """robots.txt is matched against what goes on the wire: `Disallow: /x/` spares /x.

        Callers pass same-site links only (seeds are filtered by discovery, page links by
        `_process`); the same composed gate (`state.allowed`: robots.txt and `target_ok`)
        also gates every redirect hop in `fetch()`. The seen check comes first: a link met
        again on later pages (most of them) must not pay for a robots match that cannot
        change anything.
        """
        if link.key in state.frontier.seen:
            return
        if state.allowed(link.url):
            state.frontier.add(link, depth)
        else:
            log.debug("skipped by policy", extra={"url": link.key})

    async def _discover(self, home: str) -> tuple[RobotsPolicy, Seeds]:
        """robots.txt first, then sitemaps — gated by the composed policy from the first
        request onward, so a `Sitemap:` line or a redirect it follows cannot reach a
        `target_ok`-refused address any more than a page link could (Finding 1)."""
        ua = self._settings.user_agent
        policy = await fetch_robots(self._client, home, ua)
        gate = self._gate(policy)
        seeds = await discover_seeds(
            self._client, home, policy.sitemaps, user_agent=ua, allowed=gate
        )
        return policy, seeds

    @staticmethod
    def _home(start_url: str) -> str:
        home = normalize(start_url)
        if home is None:
            raise ValueError(f"not a crawlable http(s) URL: {start_url!r}")
        return home


def _to_page(result: FetchResult, depth: int, url: str) -> FetchedPage:
    """`url` is the page's identity key; `result.url` was the spelling requested."""
    return FetchedPage(
        url=url,
        final_url=result.final_url,
        status=result.status,
        depth=depth,
        content_type=result.content_type,
        bytes=result.bytes,
        elapsed_ms=result.elapsed_ms,
        fetched_at=datetime.now(UTC),
        html=result.body,
        redirect_chain=result.redirect_chain,
        headers=result.headers,
        error=result.skipped,
    )

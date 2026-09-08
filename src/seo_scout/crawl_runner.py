"""Wires the dashboard API's `CrawlRunner` Protocol to the real crawler.

`cli.py` is the project's other wiring module; this one exists so the API can be handed a
crawl capability without `api/` importing `crawler/` (`crawl_runner` imports both freely,
`cli` imports `build_client` back from here). Dependency flows one way, `cli` -> here:
nothing in this module imports `cli`, or the import would be circular.
"""

from __future__ import annotations

import logging
import ssl
from asyncio import Event, Task, create_task
from collections.abc import Callable, Coroutine, Sequence
from contextlib import closing, suppress
from typing import Any

import httpx
import truststore

from seo_scout.ai.client import make_completer
from seo_scout.ai.pipeline import enrich_run
from seo_scout.api import targets
from seo_scout.audit.service import audit_run
from seo_scout.config import Settings
from seo_scout.crawler.crawler import Crawler
from seo_scout.crawler.pinning import Blocked, PinningTransport
from seo_scout.store import db

log = logging.getLogger("seo_scout.crawl_runner")

Crawl = Callable[[str, int | None], Coroutine[Any, Any, None]]


def build_client(settings: Settings, *, blocked: Blocked | None = None) -> httpx.AsyncClient:
    """An httpx client trusting the OS certificate store, for the crawler's own requests.

    `blocked`, when given, pins every connection to the address it was resolved and checked
    against (see `crawler.pinning`) — used only for dashboard-initiated crawls, where the URL
    came from a client rather than the operator. The OpenAI client does not share this client
    (`ai.client.make_completer` builds its own), so pinning here never touches AI calls.
    """
    tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # A transport passed to AsyncClient is used as-is: verify/http2 must go to the
    # transport itself, or AsyncClient silently ignores them (see httpx's _init_transport).
    transport = PinningTransport(blocked=blocked, verify=tls, http2=False)
    return httpx.AsyncClient(
        headers={"user-agent": settings.user_agent},
        timeout=settings.request_timeout,
        transport=transport,
    )


class BackgroundCrawler:
    """Runs one crawl at a time as an asyncio task, implementing the API's CrawlRunner."""

    def __init__(
        self,
        settings: Settings,
        *,
        allowlist: Sequence[str] = (),
        _crawl: Crawl | None = None,
    ) -> None:
        self._settings = settings
        self._allowlist = list(allowlist)
        self._crawl = _crawl or self._real_crawl
        self._task: Task[None] | None = None
        self._stop = Event()

    def start(self, url: str, max_pages: int | None) -> bool:
        """Begin a crawl in the background. False when one is already running.

        A finished task (however it finished) never blocks the next crawl: `done()` is
        True for a task that raised as much as for one that returned, so a crawl that
        blows up cannot wedge the runner for anyone after it.
        """
        if self._task is not None and not self._task.done():
            return False
        self._stop.clear()  # a stop belongs to the crawl it was aimed at, not the next one
        self._task = create_task(self._crawl(url, max_pages))
        return True

    def stop(self) -> bool:
        """Ask the running crawl to stop. False when there is nothing to stop.

        Cooperative, not `Task.cancel()`: the crawler notices between passes of its loop,
        finishes the fetches already in flight, and the run then takes the normal path
        through audit and the AI stage. Cancelling would abandon pages already fetched.
        """
        if self._task is None or self._task.done():
            return False
        self._stop.set()
        return True

    def stop_requested(self) -> bool:
        """What the crawler reads between passes. Public so it can be handed over as a seam."""
        return self._stop.is_set()

    async def wait(self) -> None:
        """For tests: await the running crawl, and swallow whatever it raised.

        Nothing else ever retrieves the task's exception (the real code path already
        handles its own errors, see `_real_crawl`), so a test-only raise would otherwise
        surface as an unhandled "exception never retrieved" warning once the task is
        garbage collected.
        """
        if self._task is not None:
            await _gather_quietly(self._task)

    async def _real_crawl(self, url: str, max_pages: int | None) -> None:
        """Crawl, audit, enrich. Errors are logged, never raised: nothing awaits this task."""
        settings, allowlist = self._settings, self._allowlist
        try:
            with closing(db.connect(settings.db)) as conn:
                async with build_client(settings, blocked=targets.blocked_ip) as client:
                    crawler = Crawler(settings=settings, conn=conn, client=client)
                    crawler.target_ok = lambda u: targets.ok(u, allowlist)
                    crawler.stop_requested = self.stop_requested
                    result = await crawler.run(url, max_pages=max_pages)
                if result.status == "failed":
                    return
                audit_run(conn, result.run_id)
                if settings.openai_api_key:
                    report = await enrich_run(
                        conn, result.run_id, settings, make_completer(settings)
                    )
                    log.info(
                        "ai stage finished",
                        extra={"run_id": result.run_id, **report.model_dump()},
                    )
        except Exception:
            log.exception("background crawl failed", extra={"url": url})


async def _gather_quietly(task: Task[None]) -> None:
    # Already logged (or is a test double); just stop it being reported "unretrieved".
    with suppress(Exception):
        await task

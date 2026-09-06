"""robots.txt fetching and policy. Uses protego (Scrapy's parser) for correct wildcards."""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from protego import Protego

from seo_scout.crawler.fetch import UNFETCHABLE

log = logging.getLogger("seo_scout.crawler.robots")


class RobotsPolicy:
    """Answers "may I fetch this?" plus crawl-delay and sitemap hints."""

    def __init__(
        self, parsed: Protego | None, user_agent: str, *, disallow_all: bool = False
    ) -> None:
        self._parsed = parsed
        self._ua = user_agent
        self._disallow_all = disallow_all

    @property
    def disallow_all(self) -> bool:
        """True when robots.txt could not be read safely, so nothing may be fetched."""
        return self._disallow_all

    def allowed(self, url: str) -> bool:
        if self._disallow_all:
            return False
        if self._parsed is None:
            return True
        return bool(self._parsed.can_fetch(url, self._ua))

    @property
    def crawl_delay(self) -> float | None:
        if self._parsed is None:
            return None
        delay = self._parsed.crawl_delay(self._ua)
        return float(delay) if delay is not None else None

    @property
    def sitemaps(self) -> list[str]:
        return list(self._parsed.sitemaps) if self._parsed is not None else []


async def fetch_robots(client: httpx.AsyncClient, site_url: str, user_agent: str) -> RobotsPolicy:
    """Fetch /robots.txt once per crawl.

    4xx (missing) means allow everything; 5xx or a network failure means disallow
    everything, which is the conservative reading Google documents for unreachable files.
    """
    robots_url = urljoin(site_url, "/robots.txt")
    try:
        response = await client.get(robots_url, headers={"user-agent": user_agent})
    except UNFETCHABLE as exc:  # unrequestable reads as unreachable
        log.warning("robots.txt unreachable, disallowing all", extra={"error": str(exc)})
        return RobotsPolicy(None, user_agent, disallow_all=True)
    if response.status_code >= 500:
        status = response.status_code
        log.warning("robots.txt server error, disallowing all", extra={"status": status})
        return RobotsPolicy(None, user_agent, disallow_all=True)
    if response.status_code >= 400:
        return RobotsPolicy(None, user_agent)
    return RobotsPolicy(Protego.parse(response.text), user_agent)

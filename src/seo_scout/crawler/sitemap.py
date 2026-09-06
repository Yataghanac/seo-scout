"""Seed discovery from sitemaps, falling back to the homepage."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree
from pydantic import BaseModel

from seo_scout.urls import Link, link_pair, same_site

log = logging.getLogger("seo_scout.crawler.sitemap")

MAX_SITEMAP_FILES = 50
MAX_PREFIX_GUESSES = 3


class Seeds(BaseModel):
    """Start URLs as `Link(key, url)`: the start as typed, then what the sitemaps listed."""

    pairs: list[Link]
    from_sitemap: set[str]  # identity keys the sitemaps named (the start only if listed)

    @property
    def urls(self) -> list[str]:
        """Request spellings, in crawl order."""
        return [pair.url for pair in self.pairs]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


_SITEMAP_ROOTS = {"urlset", "sitemapindex"}


def parse_sitemap(xml: str) -> tuple[list[str], list[str]] | None:
    """Return (child sitemap URLs, page URLs), or None when the text is not a sitemap.

    Malformed XML and well-formed non-sitemap XML (an HTML shell a host serves with 200
    for every path) both give None, so a soft 404 is not mistaken for an empty sitemap.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    if _local(root.tag) not in _SITEMAP_ROOTS:
        return None
    children: list[str] = []
    pages: list[str] = []
    for parent in root.iter():
        kind = _local(parent.tag)
        if kind not in {"sitemap", "url"}:
            continue
        for loc in parent:
            if _local(loc.tag) == "loc" and loc.text:
                (children if kind == "sitemap" else pages).append(loc.text.strip())
    return children, pages


async def _get_text(client: httpx.AsyncClient, url: str, user_agent: str | None) -> str | None:
    headers = {"user-agent": user_agent} if user_agent else {}
    try:
        response = await client.get(url, headers=headers, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.debug("sitemap fetch failed", extra={"url": url, "error": str(exc)})
        return None
    return response.text if response.status_code == 200 else None


def _root_guess(home: str) -> str:
    return urljoin(home, "/sitemap.xml")


def _prefix_guesses(start: str) -> list[str]:
    """Sitemap candidates under the start path, deepest segment first, at most three.

    Docs generators write the sitemap at the site's base path and one host often serves
    several such sites, so `/uv/guides/` is worth trying `/uv/guides/sitemap.xml` and then
    `/uv/sitemap.xml`. No guessing whether the last segment is a file: `/3.12` and
    `/index.html` look alike, so `/docs/index.html/sitemap.xml` costs one 404 and the walk
    moves on to `/docs/sitemap.xml`. Query strings are not part of the path.
    """
    parts = urlsplit(start)
    segments = [s for s in parts.path.split("/") if s]
    guesses: list[str] = []
    while segments and len(guesses) < MAX_PREFIX_GUESSES:
        path = "/" + "/".join(segments) + "/sitemap.xml"
        guesses.append(urlunsplit((parts.scheme, parts.netloc, path, "", "")))
        segments.pop()
    return guesses


@dataclass
class _Discovery:
    client: httpx.AsyncClient
    home: str
    user_agent: str | None
    visited: set[str] = field(default_factory=set)
    found: dict[str, str] = field(default_factory=dict)  # identity key -> sitemap spelling

    async def drain(self, start: list[str]) -> bool:
        """Fetch these sitemaps and any index children; True if at least one was readable."""
        queue, hit = deque(start), False
        while queue and len(self.visited) < MAX_SITEMAP_FILES:
            sitemap_url = queue.popleft()
            if sitemap_url in self.visited:
                continue
            self.visited.add(sitemap_url)
            text = await _get_text(self.client, sitemap_url, self.user_agent)
            if text is None or (parsed := parse_sitemap(text)) is None:
                continue
            hit = True
            children, pages = parsed
            queue.extend(c for c in children if same_site(self.home, c))
            for raw in pages:
                if (pair := link_pair(raw)) and same_site(self.home, pair[0]):
                    self.found.setdefault(*pair)
        return hit


async def discover_seeds(
    client: httpx.AsyncClient,
    site_url: str,
    robots_sitemaps: list[str],
    *,
    user_agent: str | None = None,
) -> Seeds:
    """Start URL first, then every same-site URL found in sitemaps (index files followed).

    The start path's own sitemap comes first: its segments are tried deepest-first until
    one answers, so the site the user asked for is read before a large robots.txt index
    can use up the file cap. Then the sitemaps robots.txt names (else `/sitemap.xml`) are
    drained; a guess already read is not fetched twice. URLs are kept as the sitemap
    spelled them (that is what gets requested) with their identity keys alongside.
    """
    home, start = link_pair(site_url) or Link(site_url, site_url)
    discovery = _Discovery(client, home, user_agent)
    for guess in _prefix_guesses(start):
        if await discovery.drain([guess]):
            break
    await discovery.drain(robots_sitemaps or [_root_guess(home)])
    found = discovery.found
    pairs = [Link(home, start), *(Link(k, u) for k, u in found.items() if k != home)]
    files = len(discovery.visited)
    log.info("seeds discovered", extra={"sitemap_files": files, "seeds": len(pairs)})
    return Seeds(pairs=pairs, from_sitemap=set(found))

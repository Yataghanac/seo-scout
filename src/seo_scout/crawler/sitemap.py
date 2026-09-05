"""Seed discovery from sitemaps, falling back to the homepage."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree
from pydantic import BaseModel

from seo_scout.urls import link_pair, normalize, resolve, same_site

log = logging.getLogger("seo_scout.crawler.sitemap")

MAX_SITEMAP_FILES = 50
MAX_PREFIX_GUESSES = 3


class Seeds(BaseModel):
    urls: list[str]
    from_sitemap: set[str]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """Return (child sitemap URLs, page URLs). Malformed XML yields two empty lists."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return [], []
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
    parts = urlsplit(home)
    return urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", ""))


def _prefix_guesses(start: str) -> list[str]:
    """Sitemap candidates under the start path, deepest directory first, at most three.

    Docs generators write the sitemap at the site's base path and one host often serves
    several such sites, so `/uv/guides/` is worth trying `/uv/guides/sitemap.xml` and then
    `/uv/sitemap.xml`. A trailing file segment (`/docs/index.html`) lives in its directory.
    Query strings are not part of the path; dotted directories (`/3.12/`) are.
    """
    parts = urlsplit(start)
    segments = [s for s in parts.path.split("/") if s]
    if segments and "." in segments[-1] and not parts.path.endswith("/"):
        segments.pop()
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
            if text is None:
                continue
            hit = True
            children, pages = parse_sitemap(text)
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
    """Homepage first, then every same-site URL found in sitemaps (index files followed).

    Sitemaps named by robots.txt (else `/sitemap.xml`) are read first; then the start
    path's directories are tried deepest-first until one answers. URLs are kept as the
    sitemap spelled them (that is what gets requested); `from_sitemap` holds their
    normalized keys for the audit.
    """
    home = normalize(site_url) or site_url
    start = resolve(site_url) or site_url
    discovery = _Discovery(client, home, user_agent)
    await discovery.drain(robots_sitemaps or [_root_guess(home)])
    for guess in _prefix_guesses(start):
        if guess not in discovery.visited and await discovery.drain([guess]):
            break
    found = discovery.found
    urls = [start, *(u for k, u in found.items() if k != home)]
    files = len(discovery.visited)
    log.info("seeds discovered", extra={"sitemap_files": files, "seeds": len(urls)})
    return Seeds(urls=urls, from_sitemap=set(found))
